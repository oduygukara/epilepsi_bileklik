"""
nabiz_analiz.py — MAX30102 ham ışık verisinden nabız (atım/dk) hesaplar,
varsa Apple Watch değerleriyle karşılaştırır.

Kullanım:  python3 nabiz_analiz.py veri/kXX_nabiz_TARIH.csv [cikti_adi]
           (aynı adlı ..._saat.csv dosyası varsa otomatik kullanılır)
"""
import sys, os
import numpy as np, pandas as pd
from scipy.signal import butter, filtfilt, find_peaks
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

FS = 50.0
BLOK_RENK = {"oturma": "#e6e6e2", "dinlenme": "#cde2fb", "stres": "#fbd3c2", "toparlanma": "#c6ecdc"}


def kopma_maskesi(d, pay_sn=2.0):
    """Sensörün 0 verdiği (bağlantı koptuğu) anlar ve ±pay_sn çevresi → True."""
    kopuk = (d.ir.astype(float) <= 0).values
    if not kopuk.any():
        return kopuk
    t = d.t.values
    tk = t[kopuk]
    idx = np.searchsorted(tk, t)
    yakin = np.full(len(t), np.inf)
    sol = np.clip(idx - 1, 0, len(tk) - 1); sag = np.clip(idx, 0, len(tk) - 1)
    yakin = np.minimum(np.abs(t - tk[sol]), np.abs(t - tk[sag]))
    return yakin <= pay_sn


def nabiz_hesapla(d):
    ir = d.ir.astype(float).where(d.ir.astype(float) > 0).values      # 0 = kopma → eksik
    ir = pd.Series(ir).interpolate(limit_direction="both").values
    b, a = butter(2, [0.7 / (FS / 2), 3.5 / (FS / 2)], btype="band")     # 42–210 atım/dk
    sinyal = -filtfilt(b, a, ir)                     # kalp atınca ışık azalır → tepe yukarı
    tepe, _ = find_peaks(sinyal, distance=int(FS * 0.33),          # en fazla ~180 atım/dk
                         prominence=np.percentile(np.abs(sinyal), 60))
    tt = d.t.values[tepe]
    rr = np.diff(tt)
    an = 60 / rr
    gecerli = (an > 40) & (an < 180)
    if gecerli.sum() > 3:                           # ani sıçramaları ele (ortancadan %30 sapan)
        med = pd.Series(an).rolling(7, center=True, min_periods=1).median().values
        gecerli &= np.abs(an - med) < 0.3 * med
    return sinyal, tt[1:][gecerli], an[gecerli]


def main():
    f = sys.argv[1]
    cikti = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(os.path.basename(f))[0]
    d = pd.read_csv(f)
    d["t"] = (d.zaman_ms - d.zaman_ms.iloc[0]) / 1000
    sinyal, t_atim, bpm = nabiz_hesapla(d)

    # 10 sn ortalamaları (sensör)
    d["pencere"] = (d.t // 10).astype(int)
    p = pd.DataFrame({"t": t_atim, "bpm": bpm, "pencere": (t_atim // 10).astype(int)})
    kopuk = kopma_maskesi(d)
    if kopuk.any():                                   # kopma çevresindeki atımları at
        p = p[~np.interp(p.t, d.t, kopuk.astype(float)).round().astype(bool)]
    print(f"Sensör kopması: {int((d.ir <= 0).sum())} satır ({(d.ir <= 0).mean() * 100:.1f}%), "
          f"etkilenen süre ±2 sn ile {kopuk.mean() * 100:.1f}%")
    p10 = p.groupby("pencere").bpm.mean()
    etiket = d.groupby("pencere").etiket.agg(lambda s: s.mode().iloc[0])

    # Apple Watch
    saat_f = f.replace(".csv", "_saat.csv")
    saat = None
    if os.path.exists(saat_f):
        saat = pd.read_csv(saat_f)
        hatali = saat[(saat.saat_bpm < 30) | (saat.saat_bpm > 220)]
        if len(hatali):                               # yazım hatası (ör. 8989) → dışarıda bırak
            print("Uyarı: geçersiz saat değerleri atlandı:", hatali.saat_bpm.tolist())
        saat = saat[(saat.saat_bpm >= 30) & (saat.saat_bpm <= 220)].copy()
        t0 = d.pc_zaman.iloc[0]
        saat["t"] = saat.pc_zaman - t0
        # sensör nabzı: saat değerinin okunduğu andan önceki 10 sn'nin ortalaması
        sensor, kalite = [], []
        for s_ in saat.t:
            w = p[(p.t > s_ - 10) & (p.t <= s_)].bpm
            m = (d.t > s_ - 10) & (d.t <= s_)
            kop = kopuk[m.values].mean() if m.any() else 1
            cv = w.std() / w.mean() if len(w) > 1 else np.inf
            if kop > 0.2:
                kalite.append("kopma")
            elif len(w) < 5:
                kalite.append("az atım")
            elif cv > 0.15:
                kalite.append("düzensiz")
            else:
                kalite.append("iyi")
            sensor.append(w.mean())
        saat["sensor"], saat["kalite"] = sensor, kalite
        saat["fark"] = saat.sensor - saat.saat_bpm

    print("=== Blok bazında nabız (sensör) ===")
    for e in ["oturma", "dinlenme", "stres", "toparlanma"]:
        tb = d[d.etiket == e].t
        if len(tb) == 0: continue
        m = (p.t >= tb.min()) & (p.t <= tb.max())
        print(f"  {e:<11} ort {p.bpm[m].mean():5.1f}  (min {p.bpm[m].min():.0f}, maks {p.bpm[m].max():.0f}, {m.sum()} atım)")
    if saat is not None and len(saat):
        print("\n=== Apple Watch karşılaştırması ===")
        print(saat[["etiket", "blok_sn", "saat_bpm", "sensor", "fark", "kalite"]].round(1).to_string(index=False))
        iyi = saat[saat.kalite == "iyi"]
        print(f"\nTüm okumalar     : OMH {saat.fark.abs().mean():.1f} atım/dk  ({saat.fark.notna().sum()} okuma)")
        print(f"Kaliteli okumalar: OMH {iyi.fark.abs().mean():.1f} atım/dk  ({len(iyi)}/{len(saat)} okuma, kapsama %{len(iyi) / len(saat) * 100:.0f})")
        for e, g in saat.groupby("etiket", sort=False):
            gi = g[g.kalite == "iyi"]
            print(f"  {e:<11} tümü {g.fark.abs().mean():5.1f} | kaliteli {gi.fark.abs().mean():5.1f}  ({len(gi)}/{len(g)})")
        saat.to_csv(cikti + "_karsilastirma.csv", index=False)

    # --- grafik ---
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.2), gridspec_kw={"height_ratios": [1, 1.4]})
    orta = d[d.etiket == "dinlenme"].t
    t0 = orta.min() + 10 if len(orta) else 20
    m = (d.t >= t0) & (d.t <= t0 + 8)
    a1.plot(d.t[m], sinyal[m], color="#2a78d6", lw=1.4)
    tm = (t_atim >= t0) & (t_atim <= t0 + 8)
    a1.plot(t_atim[tm], np.interp(t_atim[tm], d.t, sinyal), "o", color="#1c5cab", ms=5)
    a1.set_title("Nabız dalgası (dinlenmeden 8 sn) — her nokta bir kalp atışı", loc="left", fontsize=10)
    a1.set_yticks([]); a1.set_xlabel("sn")
    for e, g in d.groupby("etiket"):
        if e in BLOK_RENK:
            a2.axvspan(g.t.min(), g.t.max(), color=BLOK_RENK[e], lw=0)
            a2.text((g.t.min() + g.t.max()) / 2, 0.97, e, ha="center", va="top", fontsize=9,
                    transform=a2.get_xaxis_transform(), color="#333")
    a2.plot(p.t.values, pd.Series(p.bpm.values).rolling(5, center=True, min_periods=1).mean(),
            color="#2a78d6", lw=1.8, label="MAX30102 (bizim sensör)")
    if saat is not None and len(saat):
        a2.plot(saat.t, saat.saat_bpm, "s", color="#d6402b", ms=6, label="Apple Watch")
        kotu = saat[saat.kalite != "iyi"]
        if len(kotu):
            a2.plot(kotu.t, kotu.saat_bpm, "s", mfc="none", mec="#888", ms=11, label="kalite düşük (dışarıda)")
    a2.set_ylabel("Nabız (atım/dk)"); a2.set_xlabel("Süre (sn)")
    a2.legend(loc="lower right", frameon=False, fontsize=9)
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"{cikti} — nabız sensörü", x=0.01, ha="left", fontsize=12.5, fontweight="bold")
    fig.tight_layout(); fig.savefig(cikti + "_nabiz.png", dpi=150)
    print("\nGrafik:", cikti + "_nabiz.png")


if __name__ == "__main__":
    main()
