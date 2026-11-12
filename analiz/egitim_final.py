"""
egitim_final.py — Hareket modülünün SON eğitimi (10 gönüllü, veri seti sabit).

Önce egitim_hareket.py çalıştırılmış olmalı (sonuc/pencereler.csv dosyasını üretir).

Ne yapar?
  1) Veri seti: 10 kişi, 5 sn'lik pencereler, hareket öznitelikleri.
  2) Kişiye göre normalleştirme: her kişinin öznitelikleri yalnızca KENDİ günlük hareketlerine
     göre ölçeklenir (sarsma kullanılmaz). Bileklikte bu, ilk takışta kısa bir kalibrasyonla yapılır.
  3) İç içe "bir kişiyi dışarıda bırak" değerlendirmesi:
       dış döngü: bir kişi test için ayrılır, hiçbir aşamada kullanılmaz.
       iç döngü : kalan 9 kişiyle yine kişi bazlı doğrulama yapılır ve alarm eşiği seçilir.
       Seçilen eşik ve model test kişisine bir kez uygulanır.
     Eşik kuralı: günlük hareket pencerelerinde alarm < %5 olacak şekilde
     en çok sarsma bloğunu yakalayan eşik (eşitlikte yüksek olan).
  4) Alarm kuralı: olasılık ≥ eşik olan art arda 2 pencere (yaklaşık 7,5 sn).
  5) Son model 10 kişinin hepsiyle eğitilir ve sonuc/hareket_modeli.joblib olarak kaydedilir.

Kullanım:  python3 egitim_final.py
"""
import os, json
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import RandomForestClassifier

CIKTI = "sonuc"
GUNLUK = ["yazi_yazma", "telefon", "dis_fircalama", "el_sallama", "el_cirpma"]
ESIKLER = np.round(np.arange(0.20, 0.81, 0.05), 2)
GUNLUK_ALARM_SINIRI = 0.05


def model():
    return RandomForestClassifier(400, class_weight="balanced_subsample", min_samples_leaf=3,
                                  random_state=0, n_jobs=-1)


def normallestir(F, oz):
    Z = F.copy()
    for k in F.kisi.unique():
        m = (F.kisi == k) & F.sinif.isin(GUNLUK)
        med = F.loc[m, oz].median()
        iqr = (F.loc[m, oz].quantile(.75) - F.loc[m, oz].quantile(.25)).replace(0, 1)
        Z.loc[F.kisi == k, oz] = (F.loc[F.kisi == k, oz] - med) / iqr
    return Z


def alarm(olasilik, blok, esik):
    p = pd.Series(olasilik >= esik)
    return p.groupby(blok.values).transform(lambda s: s & s.shift(1, fill_value=False)).values


def olcutler(D, olasilik, esik):
    a = alarm(olasilik, D.blok, esik)
    b = pd.DataFrame({"blok": D.blok.values, "sinif": D.sinif.values, "a": a}).groupby("blok").agg(
        sinif=("sinif", "first"), a=("a", "any"))
    sb = b[b.sinif == "sarsma"]
    g = D.sinif.isin(GUNLUK).values
    return dict(sarsma_blok=int(sb.a.sum()), sarsma_blok_toplam=len(sb),
                gunluk_alarm=a[g].mean(), hareketsiz_alarm=a[(D.sinif == "hareketsiz").values].mean())


def lopo_olasilik(D, oz):
    pr = np.zeros(len(D))
    for k in D.kisi.unique():
        te = (D.kisi == k).values
        pr[te] = model().fit(D.loc[~te, oz].values, D.sinif.values[~te] == "sarsma").predict_proba(
            D.loc[te, oz].values)[:, 1]
    return pr


def esik_sec(D, pr):
    en_iyi = None
    for t in ESIKLER:
        o = olcutler(D, pr, t)
        if o["gunluk_alarm"] >= GUNLUK_ALARM_SINIRI:
            continue
        aday = (o["sarsma_blok"], t)
        en_iyi = max(en_iyi, aday) if en_iyi else aday
    return en_iyi[1] if en_iyi else ESIKLER[-1]


def main():
    F = pd.read_csv(os.path.join(CIKTI, "pencereler.csv"))
    oz = [c for c in F.columns if c not in ("kisi", "sinif")]
    F["blok"] = (F.sinif.ne(F.sinif.shift()) | F.kisi.ne(F.kisi.shift())).cumsum()
    Z = normallestir(F, oz)
    Z.to_csv(os.path.join(CIKTI, "veri_seti_final.csv"), index=False)
    print(f"Veri seti: {F.kisi.nunique()} kişi, {len(F)} pencere, {F.blok[F.sinif == 'sarsma'].nunique()} sarsma bloğu")

    # --- iç içe değerlendirme ---
    satirlar, test_olasilik, test_esik = [], np.zeros(len(Z)), np.zeros(len(Z))
    for k in sorted(Z.kisi.unique()):
        te = (Z.kisi == k).values
        ic = Z[~te].reset_index(drop=True)
        t = esik_sec(ic, lopo_olasilik(ic, oz))                 # yalnızca 9 eğitim kişisiyle
        m = model().fit(ic[oz].values, ic.sinif.values == "sarsma")
        pr = m.predict_proba(Z.loc[te, oz].values)[:, 1]
        test_olasilik[te], test_esik[te] = pr, t
        o = olcutler(Z[te], pr, t)
        satirlar.append(dict(kisi=k, esik=t, **o))
        print(f"  {k}: eşik {t:.2f} | sarsma bloğu {o['sarsma_blok']}/{o['sarsma_blok_toplam']} | "
              f"günlük alarm %{o['gunluk_alarm'] * 100:.1f}")
    K = pd.DataFrame(satirlar)
    K.to_csv(os.path.join(CIKTI, "final_kisi_bazli.csv"), index=False)

    # toplam (her kişi kendi dış-döngü eşiğiyle)
    a = np.zeros(len(Z), dtype=bool)
    for k in Z.kisi.unique():
        m = (Z.kisi == k).values
        a[m] = alarm(test_olasilik[m], Z.blok[m], test_esik[m][0])
    Z["olasilik"], Z["esik"], Z["alarm"] = test_olasilik, test_esik, a
    Z.to_csv(os.path.join(CIKTI, "final_tahmin.csv"), index=False)
    g = Z.sinif.isin(GUNLUK)
    sarsma_pencere = (Z.sinif == "sarsma")
    ozet = dict(
        kisi=int(Z.kisi.nunique()), pencere=int(len(Z)),
        sarsma_blok=f"{int(K.sarsma_blok.sum())}/{int(K.sarsma_blok_toplam.sum())}",
        sarsma_blok_orani=K.sarsma_blok.sum() / K.sarsma_blok_toplam.sum(),
        gunluk_alarm=float(Z.alarm[g].mean()),
        hareketsiz_alarm=float(Z.alarm[Z.sinif == "hareketsiz"].mean()),
        sarsma_pencere_duyarlilik=float((Z.olasilik[sarsma_pencere] >= Z.esik[sarsma_pencere]).mean()),
        hedefi_tutan_kisi=int((K.gunluk_alarm < GUNLUK_ALARM_SINIRI).sum()),
        esik_aralik=f"{K.esik.min():.2f}–{K.esik.max():.2f}",
    )
    print("\n=== SONUÇ (iç içe, kişi bazlı) ===")
    for k_, v in ozet.items():
        print(f"  {k_}: {v:.3f}" if isinstance(v, float) else f"  {k_}: {v}")
    # --- precision / recall / F1 (pencere bazında) ---
    y = (Z.sinif == "sarsma").values
    for ad, t in [("olasılık ≥ eşik", (Z.olasilik >= Z.esik).values), ("alarm kuralı (art arda 2)", Z.alarm.values.astype(bool))]:
        tp, fp, fn = (t & y).sum(), (t & ~y).sum(), (~t & y).sum()
        pr, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        f1 = 2 * pr * rc / max(pr + rc, 1e-9)
        print(f"  pencere bazında [{ad}]: precision {pr:.3f}  recall {rc:.3f}  F1 {f1:.3f}  (TP {tp}, FP {fp}, FN {fn})")
    a = Z.alarm.astype(bool)
    olay = Z[a & ~a.groupby(Z.blok).shift(1, fill_value=False)]          # her alarmın başladığı pencere
    print(f"  alarm olayları: toplam {len(olay)} | sarsmada {int((olay.sinif == 'sarsma').sum())} | "
          f"günlük harekette {int(olay.sinif.isin(GUNLUK).sum())} | hareketsizde {int((olay.sinif == 'hareketsiz').sum())}")
    print("  alarm veren günlük bloklar:",
          Z[g & Z.alarm].groupby("sinif").blok.nunique().to_dict())

    # --- son model: 10 kişinin tamamı ---
    t_son = esik_sec(Z, lopo_olasilik(Z, oz))
    son = model().fit(Z[oz].values, Z.sinif.values == "sarsma")
    joblib.dump(dict(model=son, oznitelikler=oz, esik=float(t_son), art_arda_pencere=2,
                     pencere_sn=5, kaydirma_sn=2.5, ornekleme_hz=50,
                     normallestirme="kişinin günlük hareketlerine göre medyan/IQR"),
                os.path.join(CIKTI, "hareket_modeli.joblib"))
    onem = pd.Series(son.feature_importances_, index=oz).sort_values(ascending=False)
    ozet.update(son_model_esik=float(t_son), oznitelik_onemi=onem.round(3).to_dict())
    with open(os.path.join(CIKTI, "final_ozet.json"), "w", encoding="utf-8") as f:
        json.dump(ozet, f, ensure_ascii=False, indent=2, default=float)
    print(f"\nSon model kaydedildi: sonuc/hareket_modeli.joblib (eşik {t_son:.2f})")
    print("En önemli öznitelikler:", onem.head(5).round(3).to_dict())


if __name__ == "__main__":
    main()
