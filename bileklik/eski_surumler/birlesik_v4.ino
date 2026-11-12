// birlesik_v4 — MPU6050 + MLX90614 + GSR, 50 Hz CSV çıktısı
// v4 yeniliği:
//  - I2C hattı kilitlenirse (MPU art arda 10 kez cevap vermezse) hat kurtarılır:
//    SCL 9 kez elle tıklatılır, Wire yeniden başlatılır, MPU yeniden ayarlanır.
//    (k08'de hat ~2 dk kilitli kalmış, stres ve yazı yazma bloklarında hareket ve sıcaklık kaybolmuştu)
// v3 yenilikleri:
//  - İvme aralığı ±8 g (önceden ±2 g: el çırpma ve sarsmada sınırı aşıyordu)
//  - Jiroskop aralığı ±1000 °/s (önceden ±250 °/s: tepeler sınıra dayanıyordu)
//  - Dahili alçak geçiren filtre (DLPF ~44 Hz): 50 Hz örneklemede örtüşmeyi azaltır
//  - MPU6050 uyursa (hepsi 0) ya da ivme sınırda takılı kalırsa (ardışık 25 ölçüm)
//    sensör tamamen sıfırlanıp yeniden ayarlanır
//  - Çıktı formatı aynı: kayit.py ve protokol.py değişmeden çalışır

#include <Wire.h>

const int MPU = 0x68, MLX = 0x5A, GSR_PIN = 34;
const uint32_t IMU_ARALIK = 20;    // 50 Hz
const uint32_t GSR_ARALIK = 100;   // 10 Hz
const uint32_t SIC_ARALIK = 1000;  // 1 Hz
uint32_t tImu, tGsr, tSic;

const float IVME_OLCEK = 4096.0;   // ±8 g    → 4096 ham birim = 1 g
const float JIRO_OLCEK = 32.8;     // ±1000 °/s → 32.8 ham birim = 1 °/s

float ax, ay, az, gx, gy, gz;
float deri = NAN, ortam = NAN;
int gsr = 0;
int takiliSayac = 0;
int cevapsizSayac = 0;

void hattiKurtar() {
  Wire.end();
  pinMode(21, INPUT_PULLUP);          // SDA
  pinMode(22, OUTPUT);                // SCL
  for (int i = 0; i < 9; i++) {       // takılı kalan cihaz SDA'yı bıraksın diye 9 saat darbesi
    digitalWrite(22, LOW);  delayMicroseconds(5);
    digitalWrite(22, HIGH); delayMicroseconds(5);
  }
  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(10);                // bir okuma en çok 10 ms beklesin
  mpuBaslat();
  cevapsizSayac = 0;
}

void mpuYaz(byte reg, byte deger) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.write(deger);
  Wire.endTransmission();
}

void mpuBaslat() {
  mpuYaz(0x6B, 0x80);   // tam sıfırlama (reset)
  delay(100);
  mpuYaz(0x6B, 0x01);   // uyan, saat kaynağı: jiroskop X (daha kararlı)
  mpuYaz(0x1A, 0x03);   // DLPF ~44 Hz
  mpuYaz(0x1B, 0x10);   // jiroskop ±1000 °/s
  mpuYaz(0x1C, 0x10);   // ivme ±8 g
  delay(10);
  takiliSayac = 0;
}

int16_t oku16() {
  uint8_t yuksek = Wire.read();
  uint8_t dusuk  = Wire.read();
  return (int16_t)((yuksek << 8) | dusuk);
}

void mpuOku() {
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  if (Wire.requestFrom(MPU, 14) != 14) {          // cevap yok
    ax = ay = az = gx = gy = gz = NAN;
    if (++cevapsizSayac >= 10) hattiKurtar();      // 0,2 sn cevapsız → hattı kurtar
    return;
  }
  cevapsizSayac = 0;
  int16_t hax = oku16(), hay = oku16(), haz = oku16();
  oku16();                                         // sıcaklık: atla
  int16_t hgx = oku16(), hgy = oku16(), hgz = oku16();

  bool hepsiSifir = (hax == 0 && hay == 0 && haz == 0 && hgx == 0 && hgy == 0 && hgz == 0);
  bool ivmeSinirda = (abs(hax) >= 32767 || hax == -32768) &&
                     (abs(hay) >= 32767 || hay == -32768) &&
                     (abs(haz) >= 32767 || haz == -32768);
  if (hepsiSifir || ivmeSinirda) takiliSayac++; else takiliSayac = 0;

  if (hepsiSifir || takiliSayac >= 25) {           // uyumuş ya da 0.5 sn takılı
    mpuBaslat();
    ax = ay = az = gx = gy = gz = NAN;
    return;
  }
  ax = hax / IVME_OLCEK;  ay = hay / IVME_OLCEK;  az = haz / IVME_OLCEK;
  gx = hgx / JIRO_OLCEK;  gy = hgy / JIRO_OLCEK;  gz = hgz / JIRO_OLCEK;
}

float mlxOku(byte reg) {
  Wire.beginTransmission(MLX);
  Wire.write(reg);
  Wire.endTransmission(false);
  if (Wire.requestFrom(MLX, 3) != 3) return NAN;
  uint8_t dusuk  = Wire.read();
  uint8_t yuksek = Wire.read();
  Wire.read();
  return ((yuksek << 8) | dusuk) * 0.02 - 273.15;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(10);
  analogReadResolution(12);
  delay(100);
  mpuBaslat();
  Serial.println("zaman_ms,ax,ay,az,gx,gy,gz,gsr,deri_C,ortam_C");
  tImu = tGsr = tSic = millis();
}

void loop() {
  uint32_t simdi = millis();
  if (simdi - tSic >= SIC_ARALIK) {
    tSic += SIC_ARALIK;
    deri  = mlxOku(0x07);
    ortam = mlxOku(0x06);
  }
  if (simdi - tGsr >= GSR_ARALIK) {
    tGsr += GSR_ARALIK;
    long t = 0;
    for (int i = 0; i < 8; i++) t += analogRead(GSR_PIN);
    gsr = t / 8;
  }
  if (simdi - tImu >= IMU_ARALIK) {
    tImu += IMU_ARALIK;
    mpuOku();
    Serial.print(simdi);    Serial.print(',');
    Serial.print(ax, 3);    Serial.print(',');
    Serial.print(ay, 3);    Serial.print(',');
    Serial.print(az, 3);    Serial.print(',');
    Serial.print(gx, 2);    Serial.print(',');
    Serial.print(gy, 2);    Serial.print(',');
    Serial.print(gz, 2);    Serial.print(',');
    Serial.print(gsr);      Serial.print(',');
    Serial.print(deri, 2);  Serial.print(',');
    Serial.println(ortam, 2);
  }
}
