# Kariyer Kapısı Takip v3.0

Kariyer Kapısı (kariyerkapisi.gov.tr) kamu ilanlarını RSS'ten tarar, detaylarını API'den çeker,
KPSS puan türlerini çıkarır; filtreleme, favori, not ve Excel'e aktarma sunar.

## Kurulum / Çalıştırma
    pip install -r requirements.txt
    python main.py

## Dosyalar
| Dosya | Görevi |
|---|---|
| `main.py` | Giriş noktası, logging kurulumu |
| `config.py` | Sabitler, temalar, `config.json` ayarları |
| `textutil.py` | Tarih/metin/KPSS yardımcıları (saf fonksiyonlar) |
| `db.py` | SQLite şeması (sürümlü migrasyon), sorgular |
| `feed.py` | RSS, detay API, görsel indirme (retry'lı oturum) |
| `service.py` | Tarama, detay önbelleği, önyükleme, Excel |
| `notify.py` | Opsiyonel masaüstü bildirimi |
| `ui.py` | Tkinter arayüzü |

## Notlar
- Mevcut `kariyer_kapisi_v2.db` dosyanızı bu klasöre koyun; şema otomatik yükseltilir, favori/notlar korunur.
- Eski `rss_url.txt` ilk açılışta `config.json`'a aktarılır.
- Ayarlar (`config.json`): `otomatik_dk` (tarama aralığı), `detay_onyukle` (KPSS bilgisini arka planda çek).
- Hata ayrıntıları `takip.log` dosyasındadır.
- Kısayollar: F5 tara · Ctrl+F ara · Ctrl+D favori · Enter/çift tık ilan sayfası.
