"""
Local Coder Agent (LCA) — Multi-Agent Coding Assistant
%100 yerel, Ollama tabanlı çoklu ajan kodlama sistemi.

Mimari:
  - PLANNER (hermes3): Orchestrator. Plan yapar, araçları çağırır.
  - CODER (qwen2.5-coder:7b): Saf kod motoru. Sadece kod üretir.

Güvenlik: Tüm dosya/terminal işlemleri kullanıcı onayına tabidir.
"""

import os
import sys
import re
import json
import subprocess
import requests
import time
import hashlib
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.syntax import Syntax
except ImportError:
    print("Gerekli kütüphaneler eksik. Yüklemek için: pip install rich requests")
    sys.exit(1)

# ╔═══════════════════════════════════════════════════════════════╗
# ║                      KONFİGÜRASYON                          ║
# ╚═══════════════════════════════════════════════════════════════╝

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
PLANNER_MODEL = "hermes3:latest"
CODER_MODEL = "qwen2.5-coder:7b"

# Sliding window — system prompt + son N mesaj tutulur
MAX_HISTORY_MESSAGES = 20
# Anti-loop — Planner'ın ardışık araç çağrısı döngü limiti
MAX_TOOL_ROUNDS = 15
# Timeout (saniye)
OLLAMA_TIMEOUT = 300   # 5 dk — yavaş modeller için cömert
COMMAND_TIMEOUT = 120  # 2 dk
# Oturum kayıt dizini
SESSION_DIR = Path(__file__).parent / "sessions"

# Dosya uzantısı → Rich syntax highlighting dili
SYNTAX_MAP: Dict[str, str] = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".less": "css",
    ".json": "json", ".jsonc": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".mdx": "markdown",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".bat": "batch", ".cmd": "batch", ".ps1": "powershell",
    ".sql": "sql",
    ".xml": "xml", ".svg": "xml",
    ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".dockerfile": "dockerfile",
    ".r": "r",
}

# Güvenlik — sistem dizinlerine erişim engeli
BLOCKED_PATH_PREFIXES = (
    # Windows
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\$Recycle.Bin", "C:\\System Volume Information",
    # Unix
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev",
)

console = Console()


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     YARDIMCI FONKSİYONLAR                   ║
# ╚═══════════════════════════════════════════════════════════════╝

def detect_syntax(file_path: str) -> str:
    """Dosya uzantısına göre syntax highlighting dilini döndürür."""
    ext = Path(file_path).suffix.lower()
    return SYNTAX_MAP.get(ext, "text")


def is_path_blocked(path: str) -> bool:
    """Sistem dizinlerine erişim girişimlerini tespit eder.

    Path traversal saldırılarına karşı normalize edilmiş absolute path
    üzerinden kontrol yapar.
    """
    try:
        # Path traversal'ı çöz (../../Windows gibi)
        abs_path = os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        return True  # Çözümlenemezse güvenlik açısından engelle

    for prefix in BLOCKED_PATH_PREFIXES:
        if abs_path.lower().startswith(prefix.lower()):
            return True
    return False


def check_ollama_health() -> Tuple[bool, List[str]]:
    """Ollama servisinin çalıştığını ve modellerin yüklü olduğunu doğrular.

    Returns:
        (is_connected, loaded_model_names)
    """
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        model_names = [m.get("name", "") for m in data.get("models", [])]
        return True, model_names
    except requests.exceptions.ConnectionError:
        return False, []
    except Exception:
        return False, []


def strip_markdown_fences(text: str) -> str:
    """Model çıktısındaki markdown code fence'lerini temizler.

    Modeller bazen XML etiketlerini ```xml ... ``` fence'ine sararlar.
    Bu zarflar kaldırılır, etiketler korunur.
    """
    return re.sub(r"```(?:\w+)?\s*\n?", "", text)


def confirm_action(message: str) -> bool:
    """Kullanıcıdan e/h onayı ister. Varsayılan: hayır."""
    answer = Prompt.ask(f"{message} Onaylıyor musun?", choices=["e", "h"], default="h")
    return answer.lower() == "e"


def action_signature(action: Dict[str, Any]) -> str:
    """Bir aksiyonun benzersiz hash imzasını üretir (tekrar tespiti için)."""
    raw = json.dumps(action, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def truncate(text: str, limit: int = 5000) -> str:
    """Uzun metinleri kırpar — context overflow'u önler."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (kırpıldı, toplam {len(text)} karakter)"


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     XML PARSER                               ║
# ╚═══════════════════════════════════════════════════════════════╝

def parse_xml_actions(text: str) -> List[Dict[str, Any]]:
    """Model çıktısından XML araç çağrılarını ayıklar.

    Desteklenen etiketler:
      - <read_file path="..."/>
      - <write_file path="...">content</write_file>
      - <execute_command>command</execute_command>
      - <request_code_generation task="..." context="..."/>
    """
    actions: List[Dict[str, Any]] = []
    if not text:
        return actions

    # Markdown fence'leri temizle
    cleaned = strip_markdown_fences(text)

    # read_file — self-closing
    for m in re.finditer(r'<read_file\s+path=["\'](.+?)["\']\s*/?>', cleaned, re.DOTALL):
        actions.append({"type": "read_file", "path": m.group(1).strip()})

    # write_file — opening + body + closing
    for m in re.finditer(r'<write_file\s+path=["\'](.+?)["\']>(.*?)</write_file>', cleaned, re.DOTALL):
        actions.append({
            "type": "write_file",
            "path": m.group(1).strip(),
            "content": m.group(2).strip(),
        })

    # execute_command
    for m in re.finditer(r"<execute_command>(.*?)</execute_command>", cleaned, re.DOTALL):
        actions.append({"type": "execute_command", "command": m.group(1).strip()})

    # request_code_generation — task required, context optional
    for m in re.finditer(
        r'<request_code_generation\s+task=["\'](.+?)["\']'
        r'(?:\s+context=["\'](.+?)["\'])?\s*/?>',
        cleaned,
        re.DOTALL,
    ):
        action: Dict[str, Any] = {"type": "request_code", "task": m.group(1).strip()}
        if m.group(2):
            action["context"] = m.group(2).strip()
        actions.append(action)

    return actions


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     SYSTEM PROMPT'LAR                        ║
# ╚═══════════════════════════════════════════════════════════════╝

def get_planner_prompt() -> str:
    return (
        "Sen bir Çoklu Ajan (Multi-Agent) kodlama sisteminin ana beyni olan "
        "ORCHESTRATOR/PLANNER'sın.\n"
        "Görevleri planlar ve sistemle bilgisayar üzerinden etkileşime girersin.\n"
        "Kullanıcıya yazılım geliştirme, sistem yönetimi ve karmaşık problemlerde "
        "yardımcı olan etkileşimli bir ajansın.\n\n"
        "ÖNEMLİ KURALLAR VE ARAÇ (TOOL) KULLANIMI:\n"
        "Sistemle SADECE aşağıdaki XML etiketlerini kullanarak etkileşime geçebilirsin. "
        "Etiketleri Markdown (```xml) bloğu içine ALMA!\n\n"
        '1. Dosya Okumak: <read_file path="dosya_yolu"/>\n'
        '2. Dosya Yazmak: <write_file path="dosya_yolu">kod_icerigi</write_file>\n'
        "3. Terminal Komutu Çalıştırmak: <execute_command>komut</execute_command>\n"
        "4. Kod Üretimi İsteklerini CODER motoruna göndermek: "
        '<request_code_generation task="görev_detayları" '
        'context="(opsiyonel) mevcut dosya içeriği veya ek bağlam"/>\n\n'
        "CONTEXT & CODER KULLANIMI:\n"
        "- Coder Engine'e görev gönderirken, mümkünse mevcut dosyanın içeriğini veya "
        "ilgili bağlamı context parametresine ekle.\n"
        "- Asla doğrudan uzun kod blokları yazma; kod üretimi, analizi ve optimizasyonu "
        "görevlerini DAİMA <request_code_generation> ile CODER ENGINE'e devret.\n"
        "- Coder'dan gelen kodu doğrudan <write_file> ile dosyaya uygula.\n\n"
        "KARARLILIK VE HATA YÖNETİMİ:\n"
        "- Bir dosyanın üzerine yazmadan veya silmeden önce, hedefin doğru olduğundan "
        "emin ol.\n"
        "- Eğer bir terminal komutu veya dosya işlemi kullanıcı tarafından reddedilirse "
        "(denied), ASLA aynı komutu kelimesi kelimesine tekrar deneme. Kullanıcının neden "
        "reddettiğini düşün ve alternatif bir yol bul.\n"
        "- Sonuçları dürüstçe raporla: Eğer bir işlem başarısız olduysa veya bir adımı "
        "atladıysan, bunu kullanıcıya gizlemeden belirt.\n"
        "- Bir görevi tamamladığında, başarısını doğrulamak için kodu/komutu test et.\n"
        "- Bağımsız araç çağrılarını aynı yanıt içinde arka arkaya yapabilirsin.\n\n"
        "GÜVENLİK:\n"
        "- Sistem dizinlerine (C:\\Windows, C:\\Program Files, /etc, /usr vb.) "
        "yazma/okuma yapamazsın. Bu girişimler otomatik engellenir.\n"
        "- Kullanıcının onayı olmadan hiçbir değişiklik yapılamaz."
    )


def get_coder_prompt() -> str:
    return (
        "Sen saf bir KOD MOTORU'sun (CODER ENGINE). Planlayıcı (Planner) ajan "
        "tarafından sana gönderilen görevler için yazılım mimarisini tasarlar ve "
        "kod üretirsin.\n\n"
        "KOD ÜRETİM STANDARTLARI:\n"
        "- Ürettiğin kod çevresindeki mevcut kodla uyumlu okunsun: Aynı yorum "
        "yoğunluğunu, isimlendirme kurallarını ve deyimleri (idiom) koru.\n"
        "- Sadece en iyi, en temiz ve optimize edilmiş (clean code) kodu üret.\n"
        "- Kodları yeniden kullanılabilir, basit ve verimli olacak şekilde kurgula.\n"
        "- Güvenlik açıklarına sebep olabilecek pratiklerden kaçın.\n"
        "- Asla gereksiz sohbet etme. 'İşte kodunuz:', 'Bunu şu şekilde yaptım:' "
        "gibi insansı giriş/çıkış cümleleri KULLANMA.\n"
        "- Sadece saf, çalışmaya hazır, dökümante edilmiş kodu üret ve doğrudan dön.\n"
        "- Eğer bir bağlam (context) verildiyse, mevcut koda uyumlu şekilde entegre ol."
    )


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     OllamaAgent                              ║
# ╚═══════════════════════════════════════════════════════════════╝

class OllamaAgent:
    """Ollama API üzerinden belirtilen model ile iletişim kuran ajan.

    Sliding window ile context büyümesini kontrol eder.
    """

    def __init__(self, model_name: str, system_prompt: str = None,
                 max_history: int = MAX_HISTORY_MESSAGES):
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.messages: List[Dict[str, str]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    # ── Context yönetimi ──────────────────────────────────────────

    def _trim_history(self) -> None:
        """System prompt + son N mesajı tutar, gerisini siler."""
        has_system = bool(self.system_prompt)
        conversation = self.messages[1:] if has_system else self.messages

        if len(conversation) > self.max_history:
            trimmed = conversation[-self.max_history:]
            self.messages = ([self.messages[0]] + trimmed) if has_system else trimmed

    # ── API çağrısı ───────────────────────────────────────────────

    def chat(self, prompt: str = None) -> str:
        """Modele mesaj gönderir, yanıtı döndürür.

        Timeout, bağlantı kopması ve boş yanıt durumlarını yönetir.
        """
        if prompt:
            self.messages.append({"role": "user", "content": prompt})

        self._trim_history()

        payload = {
            "model": self.model_name,
            "messages": self.messages,
            "stream": False,
        }

        try:
            response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=OLLAMA_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            reply = data.get("message", {}).get("content", "")

            if not reply:
                console.print(
                    f"[bold yellow]⚠ Model ({self.model_name}) boş yanıt döndü. "
                    f"Ollama durumunu kontrol edin.[/bold yellow]"
                )
                return ""

            self.messages.append({"role": "assistant", "content": reply})
            return reply

        except requests.exceptions.Timeout:
            console.print(
                f"[bold red]⏱ Ollama timeout ({self.model_name}): "
                f"{OLLAMA_TIMEOUT}s içinde yanıt alınamadı.[/bold red]"
            )
            return ""
        except requests.exceptions.ConnectionError:
            console.print(
                f"[bold red]🔌 Ollama bağlantısı koptu ({self.model_name}). "
                f"Ollama'nın çalıştığından emin olun.[/bold red]"
            )
            return ""
        except requests.exceptions.RequestException as e:
            console.print(f"[bold red]Ollama API Hatası ({self.model_name}):[/bold red] {e}")
            return ""

    # ── Serileştirme ──────────────────────────────────────────────

    def get_history_for_save(self) -> List[Dict[str, str]]:
        """Oturum kaydetmek için konuşma geçmişini döndürür (system prompt hariç)."""
        return self.messages[1:] if self.system_prompt else self.messages[:]

    def load_history(self, history: List[Dict[str, str]]) -> None:
        """Daha önce kaydedilmiş konuşma geçmişini yükler."""
        if self.system_prompt:
            self.messages = [self.messages[0]] + history
        else:
            self.messages = history[:]


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     SESSION MANAGER                          ║
# ╚═══════════════════════════════════════════════════════════════╝

class SessionManager:
    """Oturum konuşma geçmişini JSON dosyasına kaydeder ve yükler."""

    def __init__(self, session_dir: Path = SESSION_DIR):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def save(self, planner_history: List[Dict], coder_history: List[Dict],
             session_name: str = None) -> Path:
        """Mevcut oturumu timestamped JSON dosyasına kaydeder."""
        if not session_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_name = f"session_{timestamp}"

        filepath = self.session_dir / f"{session_name}.json"
        data = {
            "created_at": datetime.now().isoformat(),
            "planner_model": PLANNER_MODEL,
            "coder_model": CODER_MODEL,
            "planner_history": planner_history,
            "coder_history": coder_history,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath

    def load(self, filepath: Path) -> Tuple[List[Dict], List[Dict]]:
        """Kaydedilmiş bir oturumu yükler."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("planner_history", []), data.get("coder_history", [])

    def list_sessions(self) -> List[Path]:
        """Kayıtlı oturumları en yeniden eskiye sıralı listeler."""
        return sorted(self.session_dir.glob("session_*.json"), reverse=True)


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     AKSİYON HANDLER'LAR                      ║
# ╚═══════════════════════════════════════════════════════════════╝

def handle_read_file(action: Dict[str, Any]) -> Tuple[str, bool]:
    """read_file aksiyonunu işler.

    Returns:
        (feedback_string, was_denied)
    """
    path = action["path"]

    if is_path_blocked(path):
        console.print(f"[red]🛡 Güvenlik: '{path}' erişim engellendi (sistem dizini).[/red]")
        return (
            f"\n[SİSTEM: GÜVENLİK — '{path}' sistem dizininde, erişim engellendi]\n",
            False,
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        console.print(f"[dim]📄 '{path}' okundu ({len(content)} karakter).[/dim]")
        return (
            f"\n[SİSTEM: {path} başarıyla okundu ({len(content)} karakter)]\n"
            f"{truncate(content)}\n",
            False,
        )
    except FileNotFoundError:
        console.print(f"[red]Dosya bulunamadı: {path}[/red]")
        return f"\n[SİSTEM: HATA — '{path}' dosyası bulunamadı]\n", False
    except PermissionError:
        console.print(f"[red]Erişim izni yok: {path}[/red]")
        return f"\n[SİSTEM: HATA — '{path}' erişim izni yok]\n", False
    except Exception as e:
        console.print(f"[red]Dosya okuma hatası ({path}): {e}[/red]")
        return f"\n[SİSTEM: {path} okunamadı: {e}]\n", False


def handle_write_file(action: Dict[str, Any]) -> Tuple[str, bool]:
    """write_file aksiyonunu işler.

    Returns:
        (feedback_string, was_denied)
    """
    path = action["path"]
    content = action["content"]

    if is_path_blocked(path):
        console.print(f"[red]🛡 Güvenlik: '{path}' yazma engellendi (sistem dizini).[/red]")
        return (
            f"\n[SİSTEM: GÜVENLİK — '{path}' sistem dizininde, yazma engellendi]\n",
            False,
        )

    lang = detect_syntax(path)
    console.print(f"\n[bold yellow]⚠️  Hermes dosyaya yazmak istiyor: {path}[/bold yellow]")
    console.print(Syntax(content, lang, theme="monokai", line_numbers=True))

    if confirm_action("[bold red]Dosyaya yazma işlemini[/bold red]"):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            console.print(f"[green]✅ Dosya yazıldı: {path}[/green]")
            return (
                f"\n[SİSTEM: {path} başarıyla yazıldı ({len(content)} karakter)]\n",
                False,
            )
        except Exception as e:
            console.print(f"[red]Dosya yazma hatası ({path}): {e}[/red]")
            return f"\n[SİSTEM: {path} yazılırken HATA: {e}]\n", False
    else:
        console.print("[yellow]Yazma işlemi iptal edildi.[/yellow]")
        return (
            f"\n[SİSTEM: Kullanıcı '{path}' yazmayı REDDETTİ. "
            f"Aynı içerikle tekrar deneme, alternatif bir yaklaşım bul.]\n",
            True,
        )


def handle_execute_command(action: Dict[str, Any]) -> Tuple[str, bool]:
    """execute_command aksiyonunu işler.

    Returns:
        (feedback_string, was_denied)
    """
    cmd = action["command"]

    console.print(
        f"\n[bold yellow]⚠️  Hermes terminal komutu çalıştırmak istiyor:[/bold yellow]"
        f"\n[bold white on black] {cmd} [/bold white on black]"
    )

    if confirm_action("[bold red]Komutu çalıştırmayı[/bold red]"):
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT,
            )

            output_text = result.stdout
            if result.returncode != 0:
                output_text += f"\nSTDERR:\n{result.stderr}"
                console.print(
                    f"[red]Komut hatalı çalıştı (exit code: {result.returncode})[/red]"
                )
            else:
                console.print("[green]✅ Komut başarıyla çalıştırıldı.[/green]")

            if output_text.strip():
                console.print(f"[dim]Çıktı:\n{truncate(output_text.strip(), 3000)}[/dim]")

            return (
                f"\n[SİSTEM: '{cmd}' çalıştırıldı (exit: {result.returncode}). Çıktı:]\n"
                f"{truncate(output_text)}\n",
                False,
            )

        except subprocess.TimeoutExpired:
            console.print(f"[red]⏱ Komut timeout ({COMMAND_TIMEOUT}s): {cmd}[/red]")
            return (
                f"\n[SİSTEM: '{cmd}' {COMMAND_TIMEOUT}s timeout'u aştı ve durduruldu]\n",
                False,
            )
        except Exception as e:
            console.print(f"[red]Komut exception ({cmd}): {e}[/red]")
            return f"\n[SİSTEM: '{cmd}' çalıştırılırken exception: {e}]\n", False
    else:
        console.print("[yellow]Komut iptal edildi.[/yellow]")
        return (
            f"\n[SİSTEM: Kullanıcı '{cmd}' komutunu REDDETTİ. "
            f"Aynı komutu tekrar deneme, alternatif bir yaklaşım bul.]\n",
            True,
        )


def handle_request_code(action: Dict[str, Any], coder: OllamaAgent) -> Tuple[str, bool]:
    """request_code_generation aksiyonunu işler.

    Returns:
        (feedback_string, was_denied)
    """
    task = action["task"]
    context = action.get("context", "")

    task_preview = task[:120] + ("..." if len(task) > 120 else "")
    console.print(f"\n[bold green]💻 [Kod Motoru — Qwen2.5 Coder][/bold green] Görev: {task_preview}")

    # Coder'a bağlam ile zenginleştirilmiş prompt
    coder_prompt = task
    if context:
        coder_prompt = f"GÖREV: {task}\n\nMEVCUT KOD / BAĞLAM:\n{context}"

    with console.status("[bold green]Kod üretiliyor...[/bold green]", spinner="dots"):
        code_reply = coder.chat(coder_prompt)

    if code_reply:
        console.print(Panel(code_reply, title="💻 [Kod Motoru — Qwen2.5 Coder]", border_style="green"))
        return (
            f"\n[SİSTEM (CODER ENGINE çıktısı)]:\n"
            f"Görev: {task}\nÜretilen kod:\n{code_reply}\n",
            False,
        )
    else:
        return "\n[SİSTEM: CODER ENGINE boş yanıt döndü. Model durumunu kontrol edin.]\n", False


# ╔═══════════════════════════════════════════════════════════════╗
# ║                     ANA GİRİŞ NOKTASI                       ║
# ╚═══════════════════════════════════════════════════════════════╝

def run_main() -> None:
    """Ana terminal süreci — tüm iş mantığı burada çalışır."""
    console.clear()

    # ── Ollama Sağlık Kontrolü ────────────────────────────────────
    console.print("[dim]Ollama bağlantısı kontrol ediliyor...[/dim]")
    healthy, models = check_ollama_health()

    if not healthy:
        console.print(Panel(
            "[bold red]❌ Ollama'ya bağlanılamadı![/bold red]\n\n"
            "Kontrol edin:\n"
            "  1. Ollama çalışıyor mu? → [bold]ollama serve[/bold]\n"
            "  2. Port 11434 açık mı?\n"
            "  3. Güvenlik duvarı engeli var mı?",
            title="Bağlantı Hatası", border_style="red",
        ))
        sys.exit(1)

    # Model kontrolleri
    planner_ok = any(PLANNER_MODEL in m for m in models)
    coder_ok = any(CODER_MODEL in m for m in models)

    missing = []
    if not planner_ok:
        missing.append(PLANNER_MODEL)
    if not coder_ok:
        missing.append(CODER_MODEL)

    if missing:
        model_line = f"[yellow]⚠ Eksik: {', '.join(missing)}[/yellow]  →  ollama pull <model>"
    else:
        model_line = "[green]✅ Her iki model yüklü[/green]"

    console.print(Panel.fit(
        "[bold cyan]🤖 Local Coder Agent (LCA) v1.1.0[/bold cyan]\n"
        "[dim]Multi-Agent Coding Assistant — %100 Yerel[/dim]\n\n"
        f"  [bold]Planner :[/bold] {PLANNER_MODEL}\n"
        f"  [bold]Coder   :[/bold] {CODER_MODEL}\n"
        f"  [bold]Ollama  :[/bold] [green]✅ Bağlı[/green]   {model_line}\n\n"
        "[dim]'exit' → Çıkış  |  'save' → Oturum kaydet  |  "
        "'sessions' → Geçmiş oturumlar  |  Ctrl+C → Durdur[/dim]",
        border_style="cyan",
    ))

    # ── Ajan ve oturum başlatma ───────────────────────────────────
    planner = OllamaAgent(PLANNER_MODEL, get_planner_prompt())
    coder = OllamaAgent(CODER_MODEL, get_coder_prompt())
    session_mgr = SessionManager()

    # Anti-loop — bu turda reddedilen aksiyonların hash seti
    denied_actions: set = set()

    try:
        while True:
            user_input = Prompt.ask("\n[bold cyan]Kullanıcı[/bold cyan]")
            stripped = user_input.strip().lower()

            # ── Özel komutlar ─────────────────────────────────────
            if stripped in ("exit", "quit", "cikis", "çıkış"):
                if planner.get_history_for_save():
                    save_path = session_mgr.save(
                        planner.get_history_for_save(),
                        coder.get_history_for_save(),
                    )
                    console.print(f"[dim]💾 Oturum kaydedildi: {save_path}[/dim]")
                console.print("[bold yellow]Sistemden çıkılıyor...[/bold yellow]")
                break

            if stripped == "save":
                save_path = session_mgr.save(
                    planner.get_history_for_save(),
                    coder.get_history_for_save(),
                )
                console.print(f"[green]💾 Oturum kaydedildi: {save_path}[/green]")
                continue

            if stripped in ("sessions", "oturumlar"):
                sessions = session_mgr.list_sessions()
                if not sessions:
                    console.print("[dim]Kayıtlı oturum yok.[/dim]")
                else:
                    console.print("[bold]Kayıtlı oturumlar:[/bold]")
                    for i, s in enumerate(sessions[:10], 1):
                        console.print(f"  {i}. {s.name}")
                continue

            # ── Planner'a gönder ──────────────────────────────────
            with console.status(
                "[bold magenta]🤖 Hermes düşünüyor...[/bold magenta]",
                spinner="dots",
            ):
                reply = planner.chat(user_input)

            if not reply:
                console.print("[yellow]Planner yanıt veremedi. Tekrar deneyin.[/yellow]")
                continue

            console.print(Panel(reply, title="🤖 [Ajan — Hermes]", border_style="magenta"))

            # ── Aksiyon Döngüsü (Anti-loop korumalı) ─────────────
            tool_round = 0

            while tool_round < MAX_TOOL_ROUNDS:
                actions = parse_xml_actions(reply)
                if not actions:
                    break

                tool_round += 1
                feedback_to_planner = ""

                for action in actions:
                    sig = action_signature(action)

                    # Daha önce reddedilen aynı aksiyonu atla
                    if sig in denied_actions:
                        feedback_to_planner += (
                            "\n[SİSTEM: Bu işlem daha önce reddedildi ve atlandı. "
                            "Farklı bir yaklaşım dene.]\n"
                        )
                        console.print(
                            "[yellow]🔄 Anti-loop: Reddedilen aksiyon atlandı.[/yellow]"
                        )
                        continue

                    # Aksiyonu tipine göre yönlendir
                    if action["type"] == "read_file":
                        fb, denied = handle_read_file(action)
                    elif action["type"] == "write_file":
                        fb, denied = handle_write_file(action)
                    elif action["type"] == "execute_command":
                        fb, denied = handle_execute_command(action)
                    elif action["type"] == "request_code":
                        fb, denied = handle_request_code(action, coder)
                    else:
                        fb, denied = f"\n[SİSTEM: Bilinmeyen aksiyon tipi: {action['type']}]\n", False

                    feedback_to_planner += fb
                    if denied:
                        denied_actions.add(sig)

                # Feedback varsa Planner'a geri bildir
                if feedback_to_planner:
                    console.print(
                        "\n[bold magenta]🤖 [Ajan — Hermes][/bold magenta] "
                        "Sonuçları değerlendiriyor..."
                    )
                    with console.status(
                        "[bold magenta]Değerlendiriliyor...[/bold magenta]",
                        spinner="dots",
                    ):
                        reply = planner.chat(
                            f"SİSTEM BİLDİRİMİ:\n{feedback_to_planner}"
                        )

                    if not reply:
                        console.print(
                            "[yellow]Planner değerlendirme yanıtı boş. "
                            "Döngü sonlandırılıyor.[/yellow]"
                        )
                        break

                    console.print(Panel(
                        reply,
                        title="🤖 [Ajan — Hermes] (Değerlendirme)",
                        border_style="magenta",
                    ))
                else:
                    break

            # Anti-loop limiti aşıldıysa bilgilendir
            if tool_round >= MAX_TOOL_ROUNDS:
                console.print(
                    f"\n[bold red]⚠ Araç döngü limiti ({MAX_TOOL_ROUNDS}) aşıldı. "
                    f"Planner'dan özet isteniyor.[/bold red]"
                )
                with console.status("[bold magenta]Özetleniyor...[/bold magenta]", spinner="dots"):
                    summary = planner.chat(
                        "SİSTEM UYARISI: Maksimum araç çağrısı döngü limitine ulaştın. "
                        "Mevcut yaklaşımını kısaca özetle ve kullanıcıdan geri bildirim iste. "
                        "Aynı işlemleri tekrar deneme."
                    )
                if summary:
                    console.print(Panel(summary, title="🤖 [Döngü Özeti]", border_style="red"))

            # Her kullanıcı turunda reddedilen aksiyon listesini sıfırla
            denied_actions.clear()

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Kullanıcı tarafından durduruldu.[/bold yellow]")
        if planner.get_history_for_save():
            try:
                save_path = session_mgr.save(
                    planner.get_history_for_save(),
                    coder.get_history_for_save(),
                )
                console.print(f"[dim]💾 Oturum otomatik kaydedildi: {save_path}[/dim]")
            except Exception:
                pass
    except Exception as e:
        console.print(f"\n[bold red]Kritik Hata:[/bold red] {e}")
        console.print(f"[dim]{traceback.format_exc()}[/dim]")


if __name__ == "__main__":
    run_main()
