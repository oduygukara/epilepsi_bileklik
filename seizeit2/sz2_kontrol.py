"""
sz2_kontrol.py — SeizeIT2: tonik-klonik nöbet anlarında nabız ve hareket gerçekten değişiyor mu?

Ne yapar?
  1) seizeit2/ klasöründeki her kayıt için EKG ve hareket (mov) EDF dosyalarını açar.
  2) Etiket dosyasından (_events.tsv) tonik-klonik nöbetlerin (sz_foc_f2b) başlangıç/bitişini alır.
     (Etiket dosyaları ilk indirmede gelmişti; aynı seizeit2/ klasöründe duruyorlar.)
  3) EKG'den kalp atışlarını bulup nabzı (atım/dk), hareketten ivme şiddetini hesaplar.
  4) Her nöbet için: nöbetten önceki 2 dakika ile nöbet sırasındaki değerleri karşılaştırır.
  5) Tablo (sonuc/sz2_nobet_ozet.csv) ve ilk 6 nöbetin grafiğini (sonuc/sz2_nobetler.png) üretir.

Dosyalar tek tek işlenir; bellek (RAM) az olsa da çalışır.

Kullanım:  python3 sz2_kontrol.py
Gerekenler: python3 -m pip install pandas numpy scipy matplotlib   (EDF okuyucu kodun içinde, ek kütüphane gerekmez)
"""
import os, glob
import numpy as np, pandas as pd
from scipy.signal import butter, filtfilt, find_peaks
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

KLASOR = "seizeit2"
CIKTI = "sonuc"
ONCE_SN, SONRA_SN = 300, 300          # grafikte nöbetten 5 dk önce ve 5 dk sonra
TABAN_SN = 120                        # karşılaştırma tabanı: nöbetten önceki 2 dk


def edf_oku(yol):
    """EDF/EDF+ dosyasını okur (ek kütüphane gerekmez). Döndürür: kanal adları, örnekleme hızları, sinyaller.
    EDF yapısı: 256 baytlık genel başlık + her kanal için 256 bayt başlık + 16 bitlik veri blokları."""
    with open(yol, "rb") as f:
        bas = f.read(256)
        bas_bayt = int(bas[184:192])
        n_blok = int(bas[236:244])
        blok_sn = float(bas[244:252])
        ns = int(bas[252:256])
        k = f.read(bas_bayt - 256)
        def alan(bas_i, boy):
            return [k[bas_i + i * boy: bas_i + (i + 1) * boy].decode("latin-1").strip() for i in range(ns)]
        o = 0
        etiket = alan(o, 16); o += 16 * ns
        o += 80 * ns + 8 * ns                       # dönüştürücü, fiziksel birim
        fmin = np.array(alan(o, 8), float); o += 8 * ns
        fmax = np.array(alan(o, 8), float); o += 8 * ns
        dmin = np.array(alan(o, 8), float); o += 8 * ns
        dmax = np.array(alan(o, 8), float); o += 8 * ns
        o += 80 * ns                                # ön filtre
        nor = np.array(alan(o, 8), int)             # her blokta kanal başına örnek sayısı
        ham = np.frombuffer(f.read(), dtype="<i2")
    blok_boy = nor.sum()
    if n_blok <= 0:
        n_blok = len(ham) // blok_boy
    ham = ham[: n_blok * blok_boy].reshape(n_blok, blok_boy)
    veri, fs, ad = [], [], []
    bas_i = 0
    for i in range(ns):
        s = ham[:, bas_i: bas_i + nor[i]].reshape(-1).astype(np.float32)
        bas_i += nor[i]
        if "annotation" in etiket[i].lower():
            continue
        olcek = (fmax[i] - fmin[i]) / (dmax[i] - dmin[i])
        veri.append((s - dmin[i]) * olcek + fmin[i])
        fs.append(nor[i] / blok_sn)
        ad.append(etiket[i])
    return ad, fs, veri


def nabiz_ekg(ekg, fs):
    """EKG'den R tepelerini bulup her atım için (zaman, atım/dk) döndürür."""
    b, a = butter(2, [5 / (fs / 2), 30 / (fs / 2)], btype="band")
    x = filtfilt(b, a, ekg)
    if np.abs(np.percentile(x, 1)) > np.abs(np.percentile(x, 99)):   # ters kutuplu EKG
        x = -x
    yukseklik = np.percentile(x, 98) * 0.5
    tepe, _ = find_peaks(x, height=yukseklik, distance=int(fs * 0.3))  # en fazla 200 atım/dk
    t = tepe / fs
    rr = np.diff(t)
    bpm = 60 / rr
    ok = (bpm > 35) & (bpm < 200)
    return t[1:][ok], bpm[ok]


def ivme_siddeti(etiket, fs, veri):
    """Hareket dosyasından ivme kanallarını seçip 1 sn'lik ivme oynaklığı (std) döndürür."""
    idx = [i for i, e in enumerate(etiket) if "acc" in e.lower()]
    if len(idx) < 3:
        idx = list(range(min(3, len(etiket))))
    idx = idx[:3]                                   # ilk modülün x, y, z ekseni
    f = int(fs[idx[0]])
    a = np.sqrt(sum(np.asarray(veri[i]) ** 2 for i in idx))
    n = len(a) // f
    std = a[: n * f].reshape(n, f).std(axis=1)
    return np.arange(n) + 0.5, std, [etiket[i] for i in idx]


def main():
    os.makedirs(CIKTI, exist_ok=True)
    olaylar = sorted(glob.glob(os.path.join(KLASOR, "sub-*", "ses-01", "eeg", "*_events.tsv")))
    satirlar, ornekler, kanal_yazildi = [], [], False
    for ev in olaylar:
        d = pd.read_csv(ev, sep="\t")
        nobet = d[d.eventType == "sz_foc_f2b"]
        if nobet.empty:
            continue
        temel = os.path.basename(ev).replace("_events.tsv", "")
        sub = temel.split("_")[0]
        ekg_yol = os.path.join(KLASOR, sub, "ses-01", "ecg", temel + "_ecg.edf")
        mov_yol = os.path.join(KLASOR, sub, "ses-01", "mov", temel + "_mov.edf")
        if not (os.path.exists(ekg_yol) and os.path.exists(mov_yol)):
            print(f"  eksik dosya, atlandı: {temel}")
            continue
        e_et, e_fs, e_v = edf_oku(ekg_yol)
        m_et, m_fs, m_v = edf_oku(mov_yol)
        if not kanal_yazildi:
            print(f"EKG kanalları: {e_et} ({e_fs[0]} Hz)")
            print(f"Hareket kanalları: {m_et} ({m_fs[0]} Hz)\n")
            kanal_yazildi = True
        t_atim, bpm = nabiz_ekg(np.asarray(e_v[0]), int(e_fs[0]))
        t_ivme, ivme, _ = ivme_siddeti(m_et, m_fs, m_v)
        del e_v, m_v

        for _, s in nobet.iterrows():
            b, son = float(s.onset), float(s.onset) + float(s.duration)
            tb = (t_atim >= b - TABAN_SN) & (t_atim < b)
            ts = (t_atim >= b) & (t_atim <= son)
            ib = (t_ivme >= b - TABAN_SN) & (t_ivme < b)
            is_ = (t_ivme >= b) & (t_ivme <= son)
            satirlar.append(dict(
                kayit=temel, sub=sub, baslangic_sn=round(b), sure_sn=round(son - b),
                uyaniklik=s.get("vigilance", ""),
                nabiz_once=np.median(bpm[tb]) if tb.any() else np.nan,
                nabiz_nobet=np.median(bpm[ts]) if ts.any() else np.nan,
                nabiz_maks=np.percentile(bpm[ts], 90) if ts.sum() > 3 else np.nan,
                ivme_once=np.median(ivme[ib]) if ib.any() else np.nan,
                ivme_nobet=np.median(ivme[is_]) if is_.any() else np.nan))
            if len(ornekler) < 6:
                pa = (t_atim > b - ONCE_SN) & (t_atim < son + SONRA_SN)
                pi = (t_ivme > b - ONCE_SN) & (t_ivme < son + SONRA_SN)
                ornekler.append((temel, b, son, t_atim[pa] - b, bpm[pa], t_ivme[pi] - b, ivme[pi]))
        print(f"  {temel}: {len(nobet)} nöbet işlendi")

    T = pd.DataFrame(satirlar)
    T["nabiz_artis"] = T.nabiz_nobet - T.nabiz_once
    T["ivme_kat"] = T.ivme_nobet / T.ivme_once
    T.to_csv(os.path.join(CIKTI, "sz2_nobet_ozet.csv"), index=False)
    print(f"\n=== {len(T)} tonik-klonik nöbet, {T['sub'].nunique()} hasta ===")
    print(f"Nabız: nöbet öncesi ortanca {T.nabiz_once.median():.0f} → nöbet sırasında {T.nabiz_nobet.median():.0f} atım/dk")
    print(f"Nabız artışı: ortanca {T.nabiz_artis.median():+.0f} atım/dk; "
          f"10'dan fazla artan nöbet: {(T.nabiz_artis > 10).sum()}/{T.nabiz_artis.notna().sum()}")
    print(f"İvme oynaklığı: nöbette öncesine göre ortanca {T.ivme_kat.median():.1f} kat; "
          f"2 kattan fazla artan nöbet: {(T.ivme_kat > 2).sum()}/{T.ivme_kat.notna().sum()}")
    print(f"Tablo: {CIKTI}/sz2_nobet_ozet.csv")

    if ornekler:
        n = min(4, len(ornekler))
        fig, eks = plt.subplots(n, 2, figsize=(11, 2.4 * n), squeeze=False)
        for i, (ad, b, son, ta, hb, ti, iv) in enumerate(ornekler[:n]):
            a1, a2 = eks[i]
            for ax in (a1, a2):
                ax.axvspan(0, son - b, color="#fbd3c2", lw=0)
                ax.spines[["top", "right"]].set_visible(False)
            a1.plot(ta, pd.Series(hb).rolling(5, center=True, min_periods=1).mean(), color="#d6402b", lw=1.4)
            a1.set_ylabel("nabız (atım/dk)", fontsize=8)
            a1.set_title(f"{ad} — nabız", loc="left", fontsize=9)
            a2.plot(ti, iv, color="#2a78d6", lw=1)
            a2.set_ylabel("ivme oynaklığı (g)", fontsize=8)
            a2.set_title(f"hareket — turuncu: nöbet ({son - b:.0f} sn)", loc="left", fontsize=9)
        for ax in eks[-1]:
            ax.set_xlabel("Nöbet başlangıcına göre süre (sn)")
        fig.suptitle("SeizeIT2 — tonik-klonik nöbetlerde nabız ve hareket",
                     x=0.01, ha="left", fontsize=12, fontweight="bold")
        fig.tight_layout()
        fig.savefig(os.path.join(CIKTI, "sz2_nobetler.png"), dpi=130)
        print(f"Grafik: {CIKTI}/sz2_nobetler.png")


if __name__ == "__main__":
    main()
