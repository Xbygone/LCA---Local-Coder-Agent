# 🤖 Local Coder Agent (LCA)

Bilgisayarınızda **%100 yerel** olarak çalışan, Ollama tabanlı çoklu ajan (Multi-Agent) kodlama asistanı.

## Mimari

| Ajan | Model | Rol |
|------|-------|-----|
| **Planner / Orchestrator** | `hermes3:latest` | Ana beyin. Plan yapar, araçları çağırır, sistemi yönetir. Asla uzun kod yazmaz. |
| **Coder Engine** | `qwen2.5-coder:7b` | Saf kod motoru. Sadece kod üretir — sıfır sohbet. |

## 🚀 Özellikler

- **XML Tabanlı Araç Çağrısı**: Dosya okuma/yazma, terminal komutları ve kod üretimi istekleri sıkı XML etiketleriyle yönetilir.
- **Güvenli Dosya ve Terminal Erişimi**: Kritik eylemler öncesinde kullanıcı onayı (`e/h`) istenir. Onaysız işlem yapılamaz.
- **Sliding Window Context**: Konuşma geçmişi otomatik kırpılır — uzun oturumlarda performans çökmesi olmaz.
- **Anti-Loop Koruması**: Reddedilen aksiyonlar takip edilir, aynı hata tekrar denenmez. Maksimum döngü limiti (15 round) uygulanır.
- **Ollama Sağlık Kontrolü**: Başlangıçta Ollama bağlantısı ve model yükleme durumu otomatik kontrol edilir.
- **Oturum Kaydetme**: Konuşma geçmişi JSON formatında `sessions/` dizinine kaydedilir. Çıkışta otomatik kayıt.
- **Timeout Yönetimi**: Ollama API (5dk) ve terminal komutları (2dk) için timeout sınırları uygulanır.
- **Güvenlik**: Sistem dizinlerine (Windows/Program Files, /etc, /usr) erişim otomatik engellenir. Path traversal koruması aktif.
- **Zengin Terminal Arayüzü**: `rich` kütüphanesi ile renkli, okunabilir konsol çıktısı ve spinner animasyonları.

## 🛠️ Kurulum

### Gereksinimler
- Python 3.8+
- [Ollama](https://ollama.com) kurulu ve çalışıyor

### Kütüphaneler
```bash
pip install rich requests
```

### Modeller
```bash
ollama pull hermes3:latest
ollama pull qwen2.5-coder:7b
```

## 🎮 Kullanım

### Hızlı Başlangıç
```bash
python multi_agent_assistant.py
```
Veya Windows'ta çift tıklayarak: `run_assistant.bat`

### Terminal Komutları
| Komut | Açıklama |
|-------|----------|
| `exit` / `quit` / `cikis` | Oturumu kaydedip çıkar |
| `save` | Oturumu anında kaydeder |
| `sessions` | Kayıtlı oturumları listeler |
| `Ctrl+C` | Acil durdurma (otomatik kayıt) |

## ⚙️ XML Etiketleri (Sistem İçin)

```xml
<!-- Dosya okuma -->
<read_file path="dosya_yolu"/>

<!-- Dosya yazma -->
<write_file path="dosya_yolu">kod_icerigi</write_file>

<!-- Terminal komutu -->
<execute_command>komut</execute_command>

<!-- Kod motoru görevi (opsiyonel context) -->
<request_code_generation task="görev" context="mevcut kod bağlamı"/>
```

## 📁 Dosya Yapısı

```
LCA - Local Coder Agent/
├── multi_agent_assistant.py   # Ana uygulama
├── run_assistant.bat           # Windows tek-tık başlatıcı
├── requirements.txt            # Python bağımlılıkları
├── sessions/                   # Oturum kayıtları (otomatik oluşur)
│   └── session_YYYYMMDD_HHMMSS.json
└── README.md
```

## 🔒 Güvenlik

- Tüm dosya yazma ve komut çalıştırma işlemleri kullanıcı onayına tabidir.
- Sistem dizinlerine erişim otomatik engellenir.
- Path traversal saldırılarına karşı yollar normalize edilir (`os.path.realpath`).
- API anahtarı veya bulut bağlantısı yoktur — tamamen yerel çalışır.
- `shell=True` kullanımı gereklidir (doğal dil komutları için) ancak kullanıcı onay mekanizması ile korunur.

## 📜 Lisans

MIT
