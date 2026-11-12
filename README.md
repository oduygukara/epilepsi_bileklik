# Epilepsi Nöbet Algılama Bilekliği

Tonik-klonik nöbetleri hareket ve nabız sinyallerinden algılayan bileklik için yazılan kodlar.

## Klasörler

| Klasör | Dosya | Ne yapar |
| --- | --- | --- |
| `bileklik/` | `birlesik_v5.ino` | ESP32 yazılımı: MPU6050, MLX90614, GSR ve MAX30102'den 50 Hz CSV çıktısı |
| `bileklik/eski_surumler/` | `birlesik_v3.ino`, `birlesik_v4.ino` | Önceki sürümler (±8 g, I2C hat kurtarma) |
| `veri_toplama/` | `kayit.py` | Seri porttan serbest kayıt |
| `veri_toplama/` | `protokol.py` | Blok blok protokol kaydı; `--nabiz` ile Apple Watch karşılaştırmalı nabız oturumu |
| `analiz/` | `kalibrasyon.py` | İvmeölçeri hareketsiz anlardan kalibre eder |
| `analiz/` | `egitim_hareket.py` | Pencereleme (5 sn) ve 11 hareket özniteliği |
| `analiz/` | `egitim_final.py` | Random Forest, iç içe kişi bazlı doğrulama, alarm eşiği |
| `analiz/` | `nabiz_analiz.py` | Sensör nabzı ile saat nabzının karşılaştırması ve sinyal kalitesi kontrolü |
| `seizeit2/` | `indir_seizeit2.sh` | SeizeIT2'den tonik-klonik nöbetli kayıtların EKG ve hareket dosyalarını indirir |
| `seizeit2/` | `sz2_kontrol.py` | Nöbet öncesi ve nöbet sırasında nabız ile hareketi karşılaştırır |

## Kullanım sırası

```bash
python3 -m pip install pandas numpy scipy scikit-learn joblib matplotlib pyserial
python3 analiz/kalibrasyon.py veri
python3 analiz/egitim_hareket.py veri
python3 analiz/egitim_final.py
```

## Veri

Gönüllü kayıtları kişisel veri olduğu için bu depoda yer almaz. SeizeIT2 açık veri setidir: [OpenNeuro ds005873](https://openneuro.org/datasets/ds005873).
