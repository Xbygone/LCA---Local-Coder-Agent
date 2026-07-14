# Multi-Agent Coding Assistant

Bu proje, yerel makinenizde çalışan ve Ollama üzerinden gücünü alan iki farklı yapay zeka modelinin (Hermes ve Qwen) senkronize çalışarak kodlama görevlerini yerine getirmesini sağlayan, tamamen otonom bir asistan sistemidir.

## 🚀 Özellikler

- **Hermes3 (Planner/Orchestrator)**: Sistem yöneticisi. Kullanıcıyla etkileşime girer, plan yapar ve sistemi yönetir. Sadece XML formatında komutlar üretir.
- **Qwen3.5 (Coder Engine)**: Sadece kod yazımına odaklanmış kod motoru. Hermes'in gönderdiği task'ları alır ve optimum kalitede kod üretir.
- **XML Tabanlı İşletim**: Güvenlik ve yapısal bütünlük için sistem eylemleri sıkı XML etiketleriyle yönetilir.
- **Güvenli Dosya ve Terminal Erişimi**: Dosya yazma veya komut çalıştırma gibi kritik eylemler öncesinde kullanıcı onayı (`e/h`) istenir. Onaysız hiçbir işlem yapılamaz.
- **Otomatik Hata Kurtarma**: Çalıştırılan komutlarda bir hata olursa (`stderr`), bu hata mesajı otomatik olarak Hermes'e geri beslenir, böylece model hatayı kendi kendine analiz edip düzeltebilir.
- **Zengin Konsol Deneyimi**: `rich` kütüphanesi kullanılarak tasarlanmış, okunabilirliği yüksek renkli terminal arayüzü.

## 🛠️ Kurulum

1. **Gereksinimler:**
   - Python 3.8+
   - [Ollama](https://ollama.com) kurulu ve arka planda çalışıyor olmalıdır.

2. **Gerekli Kütüphaneler:**
   ```bash
   pip install rich requests
   ```

3. **Modelleri İndirin:**
   Ollama üzerinden aşağıdaki modellerin indirilmiş olduğundan emin olun:
   ```bash
   ollama run hermes3:latest
   ollama run qwen3.5:9b
   ```

## 🎮 Kullanım

Projeyi çalıştırmak için aşağıdaki komutu girin:

```bash
python multi_agent_assistant.py
```

Sistem başlatıldığında `Kullanıcı` girişi istenir. İstediğiniz kodlama veya görev senaryosunu doğal bir dille yazabilirsiniz. Ajanlar kendi aralarında paslaşarak görevi tamamlayacaktır. Çıkmak için `exit`, `quit` veya `cikis` yazabilirsiniz.

## ⚙️ Desteklenen XML Etiketleri (Sistem İçin)

- Dosya okumak: `<read_file path="dosya_yolu"/>`
- Dosya yazmak: `<write_file path="dosya_yolu">kod</write_file>`
- Terminal komutu çalıştırmak: `<execute_command>komut</execute_command>`
- Kod Motoruna görev göndermek: `<request_code_generation task="görev_detayı"/>`
