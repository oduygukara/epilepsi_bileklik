"""
egitim_hareket.py — Hareket modülünün eğitimi ve kişi bazlı testi.

Ne yapar?
  1) Gönüllü kayıtlarını (veri/ klasörü) okur, geçişleri atar.
  2) Her kişinin jiroskop sapmasını kendi hareketsiz bloklarından çıkarır.
  3) Veriyi 5 sn'lik, %50 örtüşen pencerelere böler; her pencereden hareket öznitelikleri çıkarır.
  4) "Bir kişiyi dışarıda bırak" yöntemiyle eğitir ve test eder:
     her turda bir kişi hiç görülmeden test edilir, model kalan kişilerle eğitilir.
  5) Sonuçları ekrana ve sonuc/ klasörüne yazar.
  0) sonuc/kalibrasyon.json varsa (python3 kalibrasyon.py üretir) ivme verisini önce düzeltir.
  6) ASIL MODEL (ikili): "sarsma mı, değil mi?" Öznitelikler her kişinin kendi günlük
     hareketlerine göre normalleştirilir (bileklikte ilk takışta kısa bir kalibrasyonla yapılabilir).
     Alarm kuralı: olasılık ≥ 0,5 olan art arda 2 pencere (yaklaşık 7,5 sn).

Kullanım:  python3 egitim_hareket.py          (kayıtlar veri/ klasöründe)
Gerekenler: python3 -m pip install pandas numpy scikit-learn joblib
"""
import os, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix
warnings.filterwarnings("ignore")

import glob, re, sys, json

VERI = sys.argv[1] if len(sys.argv) > 1 else "veri"     # kayıtların klasörü
CIKTI = "sonuc"


def kayitlari_bul(klasor):
    """veri/ klasöründeki ana oturum kayıtlarını bulur, kişiye göre gruplar.
    Atlananlar: nabız oturumları, deneme/test kayıtları, boş dosyalar,
    ±2 g'de takılan eski yazılım kayıtları (k01'in ilk hareket kaydı)."""
    kayitlar, atlanan = {}, []
    for yol in sorted(glob.glob(os.path.join(klasor, "*.csv"))):
        ad = os.path.basename(yol)
        if "nabiz" in ad or ad.endswith("_saat.csv"):
            continue
        try:
            d = pd.read_csv(yol, usecols=["kisi", "etiket", "ax", "ay", "az"])
        except Exception:
            atlanan.append((ad, "okunamadı")); continue
        if len(d) < 1000:
            atlanan.append((ad, "boş/çok kısa")); continue
        m = re.fullmatch(r"k0*(\d+)", str(d.kisi.iloc[0]))
        if not m:
            atlanan.append((ad, f"kişi kodu '{d.kisi.iloc[0]}'")); continue
        if not d.etiket.isin(HAREKETSIZ | set(GUNLUK) | {"sarsma"}).any():
            atlanan.append((ad, "etiketli blok yok")); continue
        amax = d[["ax", "ay", "az"]].abs().max().max()
        if 1.99 <= amax <= 2.01:
            atlanan.append((ad, "ivme ±2 g'de takılı (eski yazılım)")); continue
        kisi = f"k{int(m.group(1)):02d}"                 # "k010" → "k10"
        kayitlar.setdefault(kisi, []).append(ad)
    print(f"Bulunan kayıtlar ({klasor}/):")
    for k, fs in kayitlar.items():
        print(f"  {k}: {', '.join(fs)}")
    for ad, neden in atlanan:
        print(f"  atlandı: {ad}  ({neden})")
    print()
    return kayitlar


HAREKETSIZ = {"oturma", "dinlenme", "stres", "toparlanma", "durgun"}
GUNLUK = ["yazi_yazma", "telefon", "dis_fircalama", "el_sallama", "el_cirpma"]
SINIFLAR = ["hareketsiz"] + GUNLUK + ["sarsma"]
FS, W, ADIM = 50, 250, 125          # 50 Hz, 5 sn pencere, 2,5 sn kaydırma


def oznitelik(w):
    """Bir pencereden yönden bağımsız hareket öznitelikleri."""
    gm = np.sqrt(w.gx**2 + w.gy**2 + w.gz**2).values
    am = np.sqrt(w.ax**2 + w.ay**2 + w.az**2).values
    f = np.fft.rfftfreq(W, 1 / FS)
    m = (f > 0.5) & (f < 15)
    en_iyi = (0, 0, 0)
    for c in ("gx", "gy", "gz"):                      # en güçlü eksenin frekans içeriği
        x = w[c].values - w[c].mean()
        P = np.abs(np.fft.rfft(x * np.hanning(W))) ** 2
        top = P[m].sum() + 1e-9
        aday = (top, f[m][np.argmax(P[m])], P[(f >= 3) & (f <= 8)].sum() / top)
        en_iyi = max(en_iyi, aday)
    return dict(g_ort=gm.mean(), g_sd=gm.std(), g_max=gm.max(), g_p90=np.percentile(gm, 90),
                a_ort=am.mean(), a_sd=am.std(), a_max=am.max(),
                jerk=np.abs(np.diff(am)).mean() * FS,
                f_bas=en_iyi[1], bant_3_8=en_iyi[2], g_guc=np.log10(en_iyi[0]))


def kalibrasyonu_yukle():
    """sonuc/kalibrasyon.json varsa (kalibrasyon.py üretir) ivme sapma/ölçek düzeltmesini döndürür."""
    yol = os.path.join(CIKTI, "kalibrasyon.json")
    if not os.path.exists(yol):
        print("Uyarı: sonuc/kalibrasyon.json yok → ivme verisi DÜZELTİLMEDEN kullanılıyor.\n"
              "       Önce 'python3 kalibrasyon.py' çalıştır.\n")
        return None
    with open(yol, encoding="utf-8") as f:
        k = json.load(f)
    print(f"İvme kalibrasyonu uygulanıyor: sapma {k['sapma_g']} g, ölçek {k['olcek']}\n")
    return np.array(k["sapma_g"]), np.array(k["olcek"])


KALIBRASYON = None


def kisi_pencereleri(kisi, dosyalar):
    d = pd.concat([pd.read_csv(os.path.join(VERI, f)) for f in dosyalar], ignore_index=True)
    if KALIBRASYON is not None:                            # ivme: a = (a_ham - sapma) / ölçek
        sapma, olcek = KALIBRASYON
        for i, c in enumerate(("ax", "ay", "az")):
            d[c] = (d[c] - sapma[i]) / olcek[i]
    d.loc[d.etiket.isin(HAREKETSIZ), "sinif"] = "hareketsiz"
    d.loc[d.etiket.isin(GUNLUK + ["sarsma"]), "sinif"] = d.etiket
    sap = d[d.sinif == "hareketsiz"][["gx", "gy", "gz"]].median()      # jiroskop sapması
    for c in ("gx", "gy", "gz"):
        d[c] = d[c] - sap[c]
    seq = d.etiket.ne(d.etiket.shift()).cumsum()
    satirlar, atilan = [], 0
    for _, g in d[d.sinif.notna()].groupby(seq):
        for s in range(0, len(g) - W + 1, ADIM):
            w = g.iloc[s:s + W][["ax", "ay", "az", "gx", "gy", "gz"]]
            if w.isna().any(axis=1).mean() > 0.10:          # %10'dan fazla kayıp → at
                atilan += 1
                continue
            w = w.interpolate(limit_direction="both")
            satirlar.append(dict(kisi=kisi, sinif=g.sinif.iloc[0], **oznitelik(w)))
    return satirlar, atilan


def main():
    global KALIBRASYON
    os.makedirs(CIKTI, exist_ok=True)
    KALIBRASYON = kalibrasyonu_yukle()
    tum, atilan = [], {}
    for k, fs in kayitlari_bul(VERI).items():
        r, a = kisi_pencereleri(k, fs)
        tum += r; atilan[k] = a
    F = pd.DataFrame(tum)
    F.to_csv(os.path.join(CIKTI, "pencereler.csv"), index=False)
    print(f"Toplam {len(F)} pencere, {F.kisi.nunique()} kişi. Kayıp yüzünden atılan: {sum(atilan.values())}")
    print(F.groupby("sinif").size().reindex(SINIFLAR).to_string(), "\n")

    X = F.drop(columns=["kisi", "sinif"]).values
    y = F.sinif.values
    modeller = {
        "Rastgele Orman": lambda: RandomForestClassifier(400, class_weight="balanced", random_state=0, n_jobs=-1),
        "Karar Ağacı": lambda: DecisionTreeClassifier(max_depth=8, class_weight="balanced", random_state=0),
        "Lojistik Regresyon": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")),
    }
    ozet, tahminler = [], {}
    for ad, yap in modeller.items():
        p = np.empty(len(F), dtype=object)
        for k in F.kisi.unique():                      # bir kişiyi dışarıda bırak
            te = (F.kisi == k).values
            p[te] = yap().fit(X[~te], y[~te]).predict(X[te])
        tahminler[ad] = p
        s, ps = (y == "sarsma"), (p == "sarsma")
        gunluk = np.isin(y, GUNLUK)
        ozet.append(dict(model=ad,
                         dogruluk_7_sinif=(p == y).mean(),
                         sarsma_duyarlilik=(s & ps).sum() / s.sum(),
                         sarsma_kesinlik=(s & ps).sum() / max(ps.sum(), 1),
                         gunluk_harekette_alarm=(gunluk & ps).sum() / gunluk.sum(),
                         hareketsizde_alarm=((y == "hareketsiz") & ps).sum() / (y == "hareketsiz").sum()))
    O = pd.DataFrame(ozet)
    O.to_csv(os.path.join(CIKTI, "model_karsilastirma.csv"), index=False)
    print((O.set_index("model") * 100).round(1).to_string(), "\n")

    en_iyi = O.sort_values(["sarsma_duyarlilik", "sarsma_kesinlik"], ascending=False).model.iloc[0]
    p = tahminler[en_iyi]
    kisi = []
    for k in F.kisi.unique():
        m = (F.kisi == k).values; s = y[m] == "sarsma"; ps = p[m] == "sarsma"
        g = np.isin(y[m], GUNLUK)
        kisi.append(dict(kisi=k, sarsma_pencere=s.sum(), duyarlilik=(s & ps).sum() / s.sum(),
                         gunluk_alarm=(g & ps).sum() / g.sum()))
    K = pd.DataFrame(kisi)
    K.to_csv(os.path.join(CIKTI, "kisi_bazli.csv"), index=False)
    cm = pd.DataFrame(confusion_matrix(y, p, labels=SINIFLAR), index=SINIFLAR, columns=SINIFLAR)
    cm.to_csv(os.path.join(CIKTI, "karisiklik.csv"))
    print("En iyi model:", en_iyi)
    print(K.round(3).to_string(index=False))
    print("\nKarışıklık matrisi (satır = gerçek, sütun = tahmin):\n", cm.to_string())


def ikili_model(esik=0.5):
    """Asıl model: kişiye göre normalleştirilmiş, ikili (sarsma / değil) Rastgele Orman."""
    F = pd.read_csv(os.path.join(CIKTI, "pencereler.csv"))
    oz = [c for c in F.columns if c not in ("kisi", "sinif")]
    Z = F.copy()
    for k in F.kisi.unique():                       # yalnızca günlük hareketlerle normalleştir (sarsma kullanılmaz)
        m = (F.kisi == k) & F.sinif.isin(GUNLUK)
        med = F.loc[m, oz].median()
        iqr = (F.loc[m, oz].quantile(.75) - F.loc[m, oz].quantile(.25)).replace(0, 1)
        Z.loc[F.kisi == k, oz] = (F.loc[F.kisi == k, oz] - med) / iqr
    X, y, kisi = Z[oz].values, (F.sinif == "sarsma").values, F.kisi.values
    pr = np.zeros(len(y))
    for k in np.unique(kisi):                       # bir kişiyi dışarıda bırak
        te = kisi == k
        m = RandomForestClassifier(400, class_weight="balanced_subsample", min_samples_leaf=3,
                                   random_state=0, n_jobs=-1).fit(X[~te], y[~te])
        pr[te] = m.predict_proba(X[te])[:, 1]
    F["olasilik"] = pr
    F["blok"] = (F.sinif.ne(F.sinif.shift()) | F.kisi.ne(F.kisi.shift())).cumsum()
    F["p"] = F.olasilik >= esik
    F["alarm"] = F.groupby("blok").p.transform(lambda s: s & s.shift(1, fill_value=False))
    F.to_csv(os.path.join(CIKTI, "ikili_tahmin.csv"), index=False)
    p = F.p.values
    g = F.sinif.isin(GUNLUK).values
    b = F.groupby("blok").agg(sinif=("sinif", "first"), kisi=("kisi", "first"), alarm=("alarm", "any"))
    sb, gb = b[b.sinif == "sarsma"], b[b.sinif.isin(GUNLUK)]
    print("\n=== ASIL MODEL: ikili, kişiye göre normalleştirilmiş ===")
    print(f"Pencere bazında  sarsma duyarlılık {(p & y).sum() / y.sum():.1%}  kesinlik {(p & y).sum() / p.sum():.1%}")
    print(f"Alarm kuralı (≥{esik}, art arda 2 pencere):")
    print(f"  yakalanan sarsma blokları: {sb.alarm.sum()}/{len(sb)}")
    print(f"  günlük hareket pencerelerinde alarm: {F[g].alarm.mean():.1%}   (hedef < %5)")
    print(f"  hareketsiz pencerelerde alarm: {F[F.sinif == 'hareketsiz'].alarm.mean():.1%}")
    print("  kişi başına yakalanan sarsma bloğu:", sb.groupby("kisi").alarm.sum().astype(int).to_dict())
    print("  alarm veren günlük bloklar:", gb[gb.alarm].groupby("sinif").size().to_dict())
    return F


if __name__ == "__main__":
    main()
    ikili_model()
