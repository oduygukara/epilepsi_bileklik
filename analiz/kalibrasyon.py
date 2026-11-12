"""
kalibrasyon.py — İvmeölçerin sapma ve ölçek hatasını kayıtlardan hesaplar (kendi kendine kalibrasyon).

Fikir: Bilek hareketsizken ivmeölçer yalnızca yer çekimini ölçer; büyüklüğü her zaman 1 g olmalıdır.
Gönüllülerin bileği hareketsizken farklı açılarda durduğu yüzlerce an var. Bu anların hepsinde
|(a - sapma) / ölçek| = 1 olacak şekilde her eksenin sapmasını ve ölçeğini en küçük kareler ile buluruz.
Bu, sensörü 6 farklı yöne koyarak yapılan klasik kalibrasyonun veriden yapılan karşılığıdır.

Etiket (hangi hareketin yapıldığı) KULLANILMAZ; yalnızca hareketsiz anlar kullanılır.
Tüm gönüllüler aynı sensörle ölçüldüğü için tek bir kalibrasyon herkese uygulanır.

Kullanım:  python3 kalibrasyon.py          (kayıtlar veri/ klasöründe)
Çıktı:     sonuc/kalibrasyon.json  → egitim_hareket.py bunu otomatik kullanır
"""
import os, sys, json, glob
import numpy as np, pandas as pd
from scipy.optimize import least_squares

VERI = sys.argv[1] if len(sys.argv) > 1 else "veri"
CIKTI = "sonuc"
PENCERE_MS = 2000        # 2 sn'lik parçalar
DURGUN_SD = 0.01         # bir parçada her eksenin standart sapması bundan küçükse "hareketsiz"


def hareketsiz_anlar(klasor):
    anlar = []
    for yol in sorted(glob.glob(os.path.join(klasor, "*.csv"))):
        ad = os.path.basename(yol)
        if "nabiz" in ad or ad.endswith("_saat.csv"):
            continue
        try:
            d = pd.read_csv(yol, usecols=["zaman_ms", "ax", "ay", "az"]).dropna()
        except Exception:
            continue
        if len(d) < 1000:
            continue
        amax = d[["ax", "ay", "az"]].abs().max().max()
        if 1.99 <= amax <= 2.01:                     # ±2 g'de takılan eski kayıt
            continue
        for _, w in d.groupby(d.zaman_ms // PENCERE_MS):
            if len(w) > 80 and w[["ax", "ay", "az"]].std().max() < DURGUN_SD:
                anlar.append(w[["ax", "ay", "az"]].mean().values)
    return np.array(anlar)


def main():
    P = hareketsiz_anlar(VERI)
    if len(P) < 50:
        print("HATA: yeterli hareketsiz an bulunamadı."); sys.exit(1)
    ham = np.linalg.norm(P, axis=1)

    def artik(p):
        return np.linalg.norm((P - p[:3]) / p[3:], axis=1) - 1.0
    sonuc = least_squares(artik, [0, 0, 0, 1, 1, 1])
    sapma, olcek = sonuc.x[:3], sonuc.x[3:]
    duz = np.linalg.norm((P - sapma) / olcek, axis=1)

    print(f"Hareketsiz an sayısı: {len(P)}")
    print(f"Düzeltmeden önce |a|: min {ham.min():.3f}  ortanca {np.median(ham):.3f}  maks {ham.max():.3f} g  (olması gereken 1,000)")
    print(f"Sapma  (g):  x {sapma[0]:+.3f}   y {sapma[1]:+.3f}   z {sapma[2]:+.3f}")
    print(f"Ölçek     :  x {olcek[0]:.3f}    y {olcek[1]:.3f}    z {olcek[2]:.3f}")
    print(f"Düzeltmeden sonra |a|: ortanca {np.median(duz):.3f}  std {duz.std():.3f}  min {duz.min():.3f}  maks {duz.max():.3f} g")

    os.makedirs(CIKTI, exist_ok=True)
    with open(os.path.join(CIKTI, "kalibrasyon.json"), "w", encoding="utf-8") as f:
        json.dump({"sapma_g": sapma.round(4).tolist(), "olcek": olcek.round(4).tolist(),
                   "hareketsiz_an": int(len(P)),
                   "once_ortanca_g": round(float(np.median(ham)), 3),
                   "sonra_std_g": round(float(duz.std()), 4),
                   "formul": "a_duzeltilmis = (a_ham - sapma) / olcek"}, f, ensure_ascii=False, indent=2)
    print("\nKaydedildi: sonuc/kalibrasyon.json")


if __name__ == "__main__":
    main()
