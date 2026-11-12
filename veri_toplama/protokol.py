"""
protokol.py — Veri toplama protokolünü tek komutla, otomatik etiketleyerek kaydeder.

Kullanım (Terminal'de, epilepsi_veri klasöründe):
    python3 protokol.py --kisi k01              # tam protokol (fizyolojik bloklar)
    python3 protokol.py --kisi k01 --hareket    # + hareket blokları
    python3 protokol.py --kisi test --kisa      # deneme: süreler 1/4, oturma 30 sn
    python3 protokol.py --kisi k01 --sadece-hareket --tekrar 3   # sadece hareketler, 3 tur
    python3 protokol.py --kisi k09 --nabiz      # nabız oturumu (MAX30102 + GSR, ~10 dk)
                                                # her 15 sn'de bip: Apple Watch nabzını yaz + Enter

Her blok başlamadan önce talimat ekrana yazılır; hazır olunca Enter'a basılır.
Bloklar arasındaki veri "gecis" etiketiyle kaydedilir (analizde atılır).

Çıktı:
    veri/<kisi>_oturum_<tarih_saat>.csv    (tüm oturum, etiket sütunlu)
    veri/<kisi>_oturum_<tarih_saat>.json   (blokların başlangıç/bitiş zamanları)
    veri/<kisi>_nabiz_<tarih_saat>.csv     (--nabiz modunda; ESP32 verisi, ir/kirmizi sütunlu)
    veri/<kisi>_nabiz_<tarih_saat>_saat.csv (--nabiz modunda; Apple Watch değerleri)

Tüm CSV'ler aynı sütun düzenindedir; takılı olmayan sensörün sütunu boş kalır.
Böylece ana oturumlar ve nabız oturumları tek bir veri setinde birleştirilebilir.
"""

import argparse
import csv
import glob
import json
import os
import sys
import threading
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("HATA: pyserial kurulu değil:  python3 -m pip install pyserial")
    sys.exit(1)

BAUD = 115200
GSR_TEMAS_YOK = 2300   # bantlar cilde değmezken modül ~2350–2360 okuyor
SUTUNLAR = ["zaman_ms", "ax", "ay", "az", "gx", "gy", "gz", "gsr", "deri_C", "ortam_C", "ir", "kirmizi"]
ESKI_SUTUN_SAYISI = 10      # v3/v4 yazılımı ir/kirmizi göndermez → boş doldurulur
SAAT_ARALIK = 15            # nabız modunda Apple Watch sorma aralığı (sn)

# (etiket, süre_sn, talimat)
FIZYOLOJIK = [
    ("oturma", 300,
     "Elektrotların cilde oturması için sakin otur. Konuşma, kolunu kıpırdatma.\n"
     "  (Bu blok GSR tabanının oturması içindir.)"),
    ("dinlenme", 120,
     "Sakin otur, normal nefes al. Kolunu masada hareketsiz tut."),
    ("stres", 120,
     "1000'den geriye 7'şer SESLİ say: 1000, 993, 986, ...\n"
     "  Hata yaparsan 1000'den yeniden başla. Kolunu kıpırdatma."),
    ("toparlanma", 120,
     "Sakin otur, derin ve yavaş nefes al. Kolunu kıpırdatma."),
]

NABIZ = [
    ("oturma", 180,
     "Sakin otur. Sensörlü eli masada hareketsiz tut. (GSR elektrotları otursun.)"),
    ("dinlenme", 120,
     "Sakin otur, normal nefes al. Sensörlü eli kıpırdatma."),
    ("stres", 120,
     "1000'den geriye 7'şer SESLİ say: 1000, 993, 986, ...\n"
     "  Hata yaparsan 1000'den yeniden başla. Sensörlü eli kıpırdatma."),
    ("toparlanma", 120,
     "Sakin otur, derin ve yavaş nefes al. Sensörlü eli kıpırdatma."),
]

HAREKET_BASI = [
    ("durgun", 20,
     "Kolunu masaya koy ve tamamen hareketsiz tut (jiroskop sapması için referans)."),
]

HAREKET = [
    ("yazi_yazma", 45,
     "Sensörlü elinle kalemle kağıda yazı yaz."),
    ("telefon", 30,
     "Sensörlü elinle telefonu tut ve ekranda gezin / mesaj yaz."),
    ("dis_fircalama", 30,
     "Diş fırçalıyormuş gibi kolunu ağzına yakın ileri geri hareket ettir (fırçasız)."),
    ("el_sallama", 20,
     "Birine selam verir gibi elini havada salla."),
    ("el_cirpma", 20,
     "Ritmik olarak el çırp."),
    ("sarsma", 20,
     "Nöbet benzeri hareket: kolunu gevşek bırakıp hızlıca titret (saniyede 3-5 kez).\n"
     "  Kablolara ve USB'ye dikkat et, sertçe çekme."),
]


def portu_bul():
    adaylar = (glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.SLAB_USBtoUART*")
               + glob.glob("/dev/cu.wchusbserial*"))
    if not adaylar:
        print("HATA: ESP32 portu bulunamadı. USB kablosu takılı mı?")
        sys.exit(1)
    return adaylar[0]


class Kaydedici(threading.Thread):
    """Arka planda seri porttan okur ve o anki etiketle CSV'ye yazar."""

    def __init__(self, ser, yazici, kisi):
        super().__init__(daemon=True)
        self.ser, self.yazici, self.kisi = ser, yazici, kisi
        self.etiket = "baslangic"
        self.kilit = threading.Lock()
        self.sayac = 0
        self.hatali = 0
        self.son = None
        self.son_zaman = time.time()
        self.calisiyor = True

    def run(self):
        while self.calisiyor:
            try:
                ham = self.ser.readline().decode("utf-8", errors="ignore").strip()
            except serial.SerialException:
                print("\nHATA: seri bağlantı koptu (USB kablosu çıktı mı?)")
                self.calisiyor = False
                break
            if not ham:
                continue
            if ham.startswith("#"):          # ESP32 bilgi satırı (hangi sensörler var)
                print("\n  ESP32:", ham[1:].strip())
                continue
            p = ham.split(",")
            if len(p) == ESKI_SUTUN_SAYISI:
                p = p + ["nan", "nan"]
            if len(p) != len(SUTUNLAR) or not p[0].isdigit():
                if not ham.startswith("zaman_ms"):
                    self.hatali += 1
                continue
            simdi = time.time()
            with self.kilit:
                self.yazici.writerow(p + [self.kisi, self.etiket, f"{simdi:.3f}"])
                self.sayac += 1
                self.son = p
                self.son_zaman = simdi


def saat_f_flush():
    if SAAT_DOSYA[0]:
        SAAT_DOSYA[0].flush()


SAAT_DOSYA = [None]


def blok_kaydet(k, etiket, sure, talimat, sira, toplam, saat_yazici=None):
    print("\n" + "=" * 64)
    print(f"  BLOK {sira}/{toplam}:  {etiket.upper()}   ({sure:.0f} sn)")
    print("=" * 64)
    print("  " + talimat)
    with k.kilit:
        son = k.son
    if son and son[7].isdigit() and int(son[7]) >= GSR_TEMAS_YOK:
        print(f"\n  !! UYARI: GSR = {son[7]} → parmak bantları cilde değmiyor olabilir.")
        print("     Bantları kontrol et; değer düşüp sabitlenince devam et.")
    input("\n  Hazır olunca Enter'a bas... ")
    for i in (3, 2, 1):
        print(f"  {i}...", end=" ", flush=True)
        time.sleep(1)
    print("BAŞLA\a")

    with k.kilit:
        k.etiket = etiket
        bas_satir = k.sayac
    bas = time.time()
    sonraki_sor = bas + SAAT_ARALIK
    while True:
        gecen = time.time() - bas
        if gecen >= sure:
            break
        if saat_yazici is not None and time.time() >= sonraki_sor:
            an = time.time()                     # değer bu anda okunmuş sayılır
            sonraki_sor += SAAT_ARALIK
            deger = input(f"\a\n  [{gecen:4.0f} sn] Apple Watch nabzı (boş = atla): ").strip()
            while deger and not (deger.isdigit() and 30 <= int(deger) <= 220):
                deger = input("  Geçersiz değer (30–220 olmalı). Tekrar yaz (boş = atla): ").strip()
            if deger.isdigit():
                saat_yazici.writerow([f"{an:.3f}", etiket, f"{an - bas:.1f}", deger])
                saat_f_flush()
        with k.kilit:
            satir = k.sayac - bas_satir
            son = k.son
            sessiz = time.time() - k.son_zaman
        uyari = "  !! VERİ GELMİYOR" if sessiz > 3 else ""
        if son and son[7].isdigit() and int(son[7]) >= GSR_TEMAS_YOK:
            uyari += "  !! GSR TEMASI YOK"
        gsr = son[7] if son else "-"
        if saat_yazici is not None:
            ir = son[10] if son else "-"
            if son and ir.isdigit() and int(ir) < 50000:
                uyari += "  !! PARMAK/BİLEK SENSÖRDE DEĞİL"
            ek = f"ir={ir}"
        else:
            ek = f"deri={son[8] if son else '-'}"
        print(f"\r  {gecen:5.0f}/{sure:.0f} sn | {satir} satır | gsr={gsr} {ek}{uyari}     ",
              end="", flush=True)
        if not k.calisiyor:
            break
        time.sleep(0.25)

    with k.kilit:
        k.etiket = "gecis"
        satir = k.sayac - bas_satir
    bit = time.time()
    print(f"\n  Blok bitti \a  ({satir} satır, {satir / max(bit - bas, 1e-6):.1f} satır/sn)")
    return {"etiket": etiket, "baslangic_pc": round(bas, 3), "bitis_pc": round(bit, 3),
            "sure_sn": round(bit - bas, 1), "satir": satir}


def main():
    ap = argparse.ArgumentParser(description="Otomatik etiketli veri toplama protokolü")
    ap.add_argument("--kisi", required=True, help="katılımcı kodu (örn. k01)")
    ap.add_argument("--hareket", action="store_true", help="hareket bloklarını da ekle")
    ap.add_argument("--sadece-hareket", action="store_true",
                    help="fizyolojik blokları atla, sadece hareket bloklarını kaydet")
    ap.add_argument("--tekrar", type=int, default=1,
                    help="hareket bloklarını kaç tur tekrarla (varsayılan 1)")
    ap.add_argument("--baslangic", type=int, default=1,
                    help="yarım kalan kayda devam: bu blok numarasından başla (örn. 8)")
    ap.add_argument("--nabiz", action="store_true",
                    help="nabız oturumu: oturma/dinlenme/stres/toparlanma + Apple Watch girişi")
    ap.add_argument("--kisa", action="store_true", help="deneme modu: süreler 1/4")
    ap.add_argument("--port", default=None)
    args = ap.parse_args()

    hareket = list(HAREKET_BASI) + list(HAREKET) * max(1, args.tekrar)
    if args.nabiz:
        bloklar = list(NABIZ)
    elif args.sadece_hareket:
        bloklar = hareket
    else:
        bloklar = list(FIZYOLOJIK) + (hareket if args.hareket else [])
    if args.kisa:
        bloklar = [(e, 30 if e == "oturma" else max(10, s / 4), t) for e, s, t in bloklar]

    toplam_blok = len(bloklar)
    if not 1 <= args.baslangic <= toplam_blok:
        print(f"HATA: --baslangic 1 ile {toplam_blok} arasında olmalı.")
        sys.exit(1)
    ilk_no = args.baslangic
    bloklar = bloklar[ilk_no - 1:]
    if ilk_no > 1:
        print(f"Devam kaydı: {ilk_no}. bloktan başlanıyor ({bloklar[0][0]}).")

    port = args.port or portu_bul()
    os.makedirs("veri", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tur = "nabiz" if args.nabiz else "oturum"
    temel = os.path.join("veri", f"{args.kisi}_{tur}_{ts}")

    try:
        ser = serial.Serial(port, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"HATA: port açılamadı ({e}). Arduino Seri Monitör/Plotter açıksa kapat.")
        sys.exit(1)
    time.sleep(2)              # ESP32 port açılınca yeniden başlar
    ser.reset_input_buffer()

    toplam_sure = sum(s for _, s, _ in bloklar)
    print(f"\nKatılımcı: {args.kisi}   Port: {port}")
    print(f"Blok sayısı: {len(bloklar)}   Toplam kayıt süresi: ~{toplam_sure / 60:.1f} dk")
    print(f"Dosya: {temel}.csv")

    sonuclar = []
    saat_f = open(temel + "_saat.csv", "w", newline="") if args.nabiz else None
    saat_yazici = csv.writer(saat_f) if saat_f else None
    SAAT_DOSYA[0] = saat_f
    if saat_yazici:
        saat_yazici.writerow(["pc_zaman", "etiket", "blok_sn", "saat_bpm"])
        print("\nNabız modu: Apple Watch'ı öteki bileğe tak ve ANTRENMAN modunu başlat.")
        print(f"Her {SAAT_ARALIK} sn'de bip sesi gelince saatteki nabzı yazıp Enter'a bas.")
    with open(temel + ".csv", "w", newline="") as f:
        yazici = csv.writer(f)
        yazici.writerow(SUTUNLAR + ["kisi", "etiket", "pc_zaman"])
        k = Kaydedici(ser, yazici, args.kisi)
        k.start()
        time.sleep(1)
        if k.sayac == 0:
            print("\nUYARI: ESP32'den henüz veri gelmiyor. Kod yüklü mü, kablo takılı mı?")
        try:
            for i, (etiket, sure, talimat) in enumerate(bloklar, ilk_no):
                sonuclar.append(blok_kaydet(k, etiket, sure, talimat, i, toplam_blok, saat_yazici))
                if not k.calisiyor:
                    break
        except (KeyboardInterrupt, EOFError):
            print("\n\nKullanıcı durdurdu. Şimdiye kadarki veri kaydedildi.")
        finally:
            k.calisiyor = False
            time.sleep(0.3)
            ser.close()
            if saat_f:
                saat_f.close()

    meta = {"kisi": args.kisi, "tarih": ts, "port": port, "kisa_mod": args.kisa, "oturum": tur,
            "toplam_satir": k.sayac, "hatali_satir": k.hatali, "bloklar": sonuclar}
    with open(temel + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 64)
    print("  OTURUM BİTTİ")
    print("=" * 64)
    for b in sonuclar:
        print(f"  {b['etiket']:<14} {b['sure_sn']:>6.1f} sn  {b['satir']:>6} satır")
    print(f"  Atlanan hatalı satır: {k.hatali}")
    print(f"  Dosyalar: {temel}.csv  +  .json")


if __name__ == "__main__":
    main()
