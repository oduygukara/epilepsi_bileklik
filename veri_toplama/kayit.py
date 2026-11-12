"""
kayit.py — ESP32'den gelen sensör verisini etiketli CSV dosyasına kaydeder.

Kullanım (Terminal'de):
    python3 kayit.py --kisi k01 --etiket test --sure 30

Parametreler:
    --kisi    Katılımcı kodu (örn. k01)
    --etiket  Bu kayıttaki durum (örn. dinlenme, yurume, dis_fircalama, sarsma)
    --sure    Kayıt süresi (saniye). Verilmezse Ctrl+C'ye basana kadar kaydeder.
    --port    Seri port (verilmezse otomatik bulunur)

Kayıtlar "veri" klasörüne şu adla yazılır:
    veri/<kisi>_<etiket>_<tarih_saat>.csv
"""

import argparse
import csv
import glob
import os
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("HATA: pyserial kurulu değil. Terminal'de şunu çalıştır:")
    print("    python3 -m pip install pyserial")
    sys.exit(1)

BAUD = 115200
BEKLENEN_SUTUNLAR = ["zaman_ms", "ax", "ay", "az", "gx", "gy", "gz",
                     "gsr", "deri_C", "ortam_C"]


def portu_bul():
    """Mac'te ESP32'nin seri portunu bulur."""
    adaylar = (glob.glob("/dev/cu.usbserial*")
               + glob.glob("/dev/cu.SLAB_USBtoUART*")
               + glob.glob("/dev/cu.wchusbserial*"))
    if not adaylar:
        print("HATA: ESP32 portu bulunamadı. USB kablosu takılı mı?")
        sys.exit(1)
    return adaylar[0]


def satir_gecerli_mi(parcalar):
    """Satırda 10 sütun var mı ve ilk sütun bir sayı mı?"""
    if len(parcalar) != len(BEKLENEN_SUTUNLAR):
        return False
    try:
        int(parcalar[0])
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser(description="ESP32 sensör verisini CSV'ye kaydeder")
    ap.add_argument("--kisi", required=True, help="katılımcı adı/kodu")
    ap.add_argument("--etiket", required=True, help="durum etiketi")
    ap.add_argument("--sure", type=float, default=None, help="kayıt süresi (sn)")
    ap.add_argument("--port", default=None, help="seri port")
    args = ap.parse_args()

    port = args.port or portu_bul()
    os.makedirs("veri", exist_ok=True)
    zaman_damgasi = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya_adi = os.path.join("veri", f"{args.kisi}_{args.etiket}_{zaman_damgasi}.csv")

    print(f"Port: {port}")
    print(f"Dosya: {dosya_adi}")

    try:
        ser = serial.Serial(port, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"HATA: port açılamadı ({e}).")
        print("Arduino IDE'de Seri Monitör ya da Plotter açıksa kapat ve tekrar dene.")
        sys.exit(1)

    # Port açılınca ESP32 yeniden başlar; açılış mesajlarının geçmesini bekle
    time.sleep(2)
    ser.reset_input_buffer()

    satir_sayisi = 0
    hatali_satir = 0
    baslangic = None
    son_rapor = time.time()

    with open(dosya_adi, "w", newline="") as f:
        yazici = csv.writer(f)
        yazici.writerow(BEKLENEN_SUTUNLAR + ["kisi", "etiket", "pc_zaman"])

        print("Kayıt başladı. Durdurmak için Ctrl+C.\n")
        try:
            while True:
                ham = ser.readline().decode("utf-8", errors="ignore").strip()
                if not ham:
                    continue
                parcalar = ham.split(",")
                if not satir_gecerli_mi(parcalar):
                    if not ham.startswith("zaman_ms"):   # başlık satırını sayma
                        hatali_satir += 1
                    continue

                simdi = time.time()
                if baslangic is None:
                    baslangic = simdi
                yazici.writerow(parcalar + [args.kisi, args.etiket, f"{simdi:.3f}"])
                satir_sayisi += 1

                gecen = simdi - baslangic
                if simdi - son_rapor >= 1:
                    print(f"\r{gecen:6.1f} sn | {satir_sayisi} satır | "
                          f"gsr={parcalar[7]} deri={parcalar[8]} az={parcalar[3]}   ",
                          end="", flush=True)
                    son_rapor = simdi

                if args.sure is not None and gecen >= args.sure:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()

    sure = (time.time() - baslangic) if baslangic else 0
    hiz = satir_sayisi / sure if sure > 0 else 0
    print("\n\nKayıt bitti.")
    print(f"  Süre: {sure:.1f} sn")
    print(f"  Satır: {satir_sayisi}  (ortalama {hiz:.1f} satır/sn, hedef 50)")
    print(f"  Atlanan hatalı satır: {hatali_satir}")
    print(f"  Dosya: {dosya_adi}")


if __name__ == "__main__":
    main()
