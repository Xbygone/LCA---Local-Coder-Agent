import os
import sys
import re
import json
import subprocess
import requests
import time
from typing import List, Dict, Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.syntax import Syntax
except ImportError:
    print("Lütfen gerekli kütüphaneleri yükleyin: pip install rich requests")
    exit(1)

# --- KONFİGÜRASYON ---
OLLAMA_API_URL = "http://localhost:11434/api/chat"
PLANNER_MODEL = "hermes3:latest"
CODER_MODEL = "qwen3.5:9b"
STATE_FILE = "state.json"

console = Console()

class OllamaAgent:
    """Ollama API üzerinden belirtilen model ile iletişim kuran ajan sınıfı."""
    def __init__(self, model_name: str, system_prompt: str = None):
        self.model_name = model_name
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})
            
    def load_messages(self, messages: List[Dict[str, str]]):
        self.messages = messages

    def chat(self, prompt: str = None) -> str:
        if prompt:
            self.messages.append({"role": "user", "content": prompt})
            
        payload = {
            "model": self.model_name,
            "messages": self.messages,
            "stream": False
        }
        
        try:
            response = requests.post(OLLAMA_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            reply = data.get("message", {}).get("content", "")
            
            self.messages.append({"role": "assistant", "content": reply})
            return reply
        except requests.exceptions.RequestException as e:
            console.print(f"[bold red]Ollama API Hatası ({self.model_name}):[/bold red] {e}")
            return ""

def confirm_action(message: str) -> bool:
    """Kullanıcıdan (e/h) onayı alır."""
    answer = Prompt.ask(f"{message} Onaylıyor musun?", choices=["e", "h"], default="h")
    return answer.lower() == "e"

def parse_xml_actions(text: str) -> List[Dict[str, Any]]:
    """Ajanın çıktısındaki XML etiketlerini Regex ile ayrıştırır."""
    actions = []
    
    # <read_file path="..."/>
    read_pattern = re.compile(r'<read_file\s+path=["\'](.*?)["\']\s*/?>')
    for match in read_pattern.finditer(text):
        actions.append({"type": "read_file", "path": match.group(1)})
        
    # <write_file path="...">...</write_file>
    write_pattern = re.compile(r'<write_file\s+path=["\'](.*?)["\']>(.*?)</write_file>', re.DOTALL)
    for match in write_pattern.finditer(text):
        actions.append({"type": "write_file", "path": match.group(1), "content": match.group(2).strip()})
        
    # <execute_command>...</execute_command>
    exec_pattern = re.compile(r'<execute_command>(.*?)</execute_command>', re.DOTALL)
    for match in exec_pattern.finditer(text):
        actions.append({"type": "execute_command", "command": match.group(1).strip()})
        
    # <request_code_generation task="..."/>
    req_pattern = re.compile(r'<request_code_generation\s+task=["\'](.*?)["\']\s*/?>', re.DOTALL)
    for match in req_pattern.finditer(text):
        actions.append({"type": "request_code", "task": match.group(1)})
        
    return actions

def get_planner_prompt():
    return """Sen bir Çoklu Ajan (Multi-Agent) kodlama sisteminin ana beyni olan ORCHESTRATOR/PLANNER'sın.
Görevleri planlar ve sistemle bilgisayar üzerinden etkileşime girersin.
Aşağıdaki XML etiketleri DIŞINDA HİÇBİR ŞEKİLDE sistemle etkileşim kuramazsın. Kesinlikle bu formatı kullanmalısın:

1. Dosya Okumak için: <read_file path="dosya_yolu"/>
2. Dosya Yazmak için: <write_file path="dosya_yolu">kod_icerigi</write_file>
3. Terminal Komutu Çalıştırmak için: <execute_command>komut</execute_command>
4. Kod Üretimi İsteklerini CODER motoruna göndermek için: <request_code_generation task="görev_detayları"/>

Düşüncelerini ve planını normal metin olarak açıklayabilirsin, fakat eylem yapmak istediğinde SADECE yukarıdaki XML etiketlerini kullan. Etiketleri Markdown (```xml) bloğu içine ALMA!"""

def get_coder_prompt():
    return """Sen saf bir KOD MOTORU'sun. (CODER ENGINE)
Sana verilen görevler için yalnızca en iyi, en temiz ve optimize edilmiş kodu üretmelisin.
Mimari tasarıma dikkat et, clean code prensiplerini uygula. 
Açıklamaları kod yorumu olarak veya en kısa ve öz şekilde yap. Gereksiz sohbet etme."""


def run_worker():
    """Yeni terminalde çalışacak olan arka plan işçi süreci."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            
        planner = OllamaAgent(PLANNER_MODEL)
        if not state.get("planner_messages"):
            # Initial state
            planner.messages = [{"role": "system", "content": get_planner_prompt()}]
        else:
            planner.load_messages(state["planner_messages"])

        coder = OllamaAgent(CODER_MODEL)
        if not state.get("coder_messages"):
            coder.messages = [{"role": "system", "content": get_coder_prompt()}]
        else:
            coder.load_messages(state["coder_messages"])
            
        user_input = state.get("current_input", "")
        
        console.print(Panel.fit(
            "[bold cyan]Multi-Agent Coding Assistant - Worker Terminal[/bold cyan]\n"
            f"[bold magenta]🤖 PLANNER:[/bold magenta] {PLANNER_MODEL}\n"
            f"[bold green]💻 CODER:[/bold green] {CODER_MODEL}",
            border_style="cyan"
        ))
        
        console.print("\n[bold magenta]🤖 [Ajan - Hermes][/bold magenta] Düşünüyor...")
        reply = planner.chat(user_input)
        
        console.print(Panel(reply, title="🤖 [Ajan - Hermes]", border_style="magenta"))
        
        # Aksiyon Döngüsü
        while True:
            actions = parse_xml_actions(reply)
            if not actions:
                break # Etiket yoksa döngüden çık, iş bitti.
                
            feedback_to_planner = ""
            
            for action in actions:
                if action["type"] == "read_file":
                    path = action["path"]
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                        feedback_to_planner += f"\n[SİSTEM: {path} dosyası başarıyla okundu]\n{content}\n"
                        console.print(f"[dim]Sistem: '{path}' dosyası okundu.[/dim]")
                    except Exception as e:
                        feedback_to_planner += f"\n[SİSTEM: {path} okunamadı]: {e}\n"
                        
                elif action["type"] == "write_file":
                    path = action["path"]
                    content = action["content"]
                    
                    console.print(f"\n[bold yellow]⚠️  Hermes dosyaya yazmak istiyor: {path}[/bold yellow]")
                    console.print(Syntax(content, "python", theme="monokai", line_numbers=True))
                    
                    if confirm_action("[bold red]Dosyaya yazma işlemini[/bold red]"):
                        try:
                            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(content)
                            feedback_to_planner += f"\n[SİSTEM: {path} dosyası başarıyla yazıldı]\n"
                            console.print(f"[green]Sistem: Dosya yazıldı ({path})[/green]")
                        except Exception as e:
                            feedback_to_planner += f"\n[SİSTEM: {path} dosyasına yazılırken HATA oluştu]: {e}\n"
                    else:
                        feedback_to_planner += f"\n[SİSTEM: Kullanıcı {path} dosyasına yazmayı REDDETTİ]\n"
                        console.print("[yellow]Sistem: Yazma işlemi iptal edildi.[/yellow]")
                        
                elif action["type"] == "execute_command":
                    cmd = action["command"]
                    
                    console.print(f"\n[bold yellow]⚠️  Hermes terminal komutu çalıştırmak istiyor:[/bold yellow]\n[bold white on black] {cmd} [/bold white on black]")
                    
                    if confirm_action("[bold red]Komutu çalıştırmayı[/bold red]"):
                        try:
                            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                            
                            output_text = result.stdout
                            if result.returncode != 0:
                                output_text += f"\nSTDERR (HATA):\n{result.stderr}"
                                console.print(f"[red]Sistem: Komut hatalı çalıştı (Kod: {result.returncode})[/red]")
                            else:
                                console.print("[green]Sistem: Komut başarıyla çalıştırıldı.[/green]")
                                
                            feedback_to_planner += f"\n[SİSTEM: Komut '{cmd}' çalıştırıldı. Çıktı:]\n{output_text}\n"
                        except Exception as e:
                            feedback_to_planner += f"\n[SİSTEM: '{cmd}' çalıştırılırken exception oluştu]: {e}\n"
                    else:
                        feedback_to_planner += f"\n[SİSTEM: Kullanıcı komutu çalıştırmayı REDDETTİ: '{cmd}']\n"
                        console.print("[yellow]Sistem: Komut iptal edildi.[/yellow]")
                        
                elif action["type"] == "request_code":
                    task = action["task"]
                    
                    console.print(f"\n[bold green]💻 [Kod Motoru - Qwen3.5][/bold green] Görev aldı: {task}")
                    code_reply = coder.chat(task)
                    
                    console.print(Panel(code_reply, title="💻 [Kod Motoru - Qwen3.5]", border_style="green"))
                    
                    feedback_to_planner += f"\n[SİSTEM (CODER ENGINE'den gelen kod)]: \nGörev: {task}\nÇıktı:\n{code_reply}\n"
            
            del actions
            
            if feedback_to_planner:
                console.print("\n[bold magenta]🤖 [Ajan - Hermes][/bold magenta] Sonuçları değerlendiriyor...")
                reply = planner.chat(f"SİSTEM BİLDİRİMİ (Kullanıcı veya İşletim Sistemi):\n{feedback_to_planner}")
                console.print(Panel(reply, title="🤖 [Ajan - Hermes] (Değerlendirme)", border_style="magenta"))
                del feedback_to_planner
            else:
                break
                
        # Görev tamamlandı, yeni durumu dosyaya kaydet
        state["planner_messages"] = planner.messages
        state["coder_messages"] = coder.messages
        state["last_reply"] = reply
        state["status"] = "success"
        
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=4)
            
        # Terminal kendi kendine kapanacak
        
    except Exception as e:
        console.print(f"\n[bold red]Beklenmeyen Hata (Worker):[/bold red] {e}")
        # Hata durumunda state'i güncelle ama terminali kapatma
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            state["status"] = "error"
            state["error_msg"] = str(e)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except:
            pass
        input("\nÇıkmak için Enter'a basın...")


def run_main():
    """Ana terminal süreci."""
    console.clear()
    console.print(Panel.fit(
        "[bold cyan]Multi-Agent Coding Assistant (Main Terminal)[/bold cyan]\n"
        "Modelin işlem adımları ayrı bir pencerede gösterilecektir.\n"
        "İşlemi iptal etmek isterseniz [bold red]Ctrl+C[/bold red] yapabilirsiniz.",
        border_style="cyan"
    ))
    
    # Başlangıç state'i
    state = {
        "planner_messages": [],
        "coder_messages": [],
        "current_input": "",
        "status": "idle",
        "last_reply": "",
        "error_msg": ""
    }
    
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]Kullanıcı[/bold cyan]")
            if user_input.strip().lower() in ['exit', 'quit', 'cikis']:
                console.print("[bold yellow]Sistemden çıkılıyor...[/bold yellow]")
                break
                
            state["current_input"] = user_input
            state["status"] = "running"
            
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
                
            console.print("[dim]Ajan ayrı pencerede başlatılıyor... Lütfen bekleyin. İptal için Ctrl+C yapabilirsiniz.[/dim]")
            
            # Yeni pencerede worker'ı başlat (Windows'a özel CREATE_NEW_CONSOLE)
            creationflags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0x00000010)
            
            # Python executable yolu
            python_executable = sys.executable
            
            process = subprocess.Popen([python_executable, __file__, "--worker"], creationflags=creationflags)
            
            # Sürecin bitmesini bekle
            while process.poll() is None:
                time.sleep(0.5)
                
            # Süreç bittikten sonra state dosyasını oku
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except FileNotFoundError:
                state["status"] = "error"
                state["error_msg"] = "state.json dosyası bulunamadı."
                
            if state["status"] == "success":
                console.print(Panel(state["last_reply"], title="🤖 [Ajan - Hermes] (Final Sonuç)", border_style="magenta"))
            elif state["status"] == "error":
                console.print(f"\n[bold red]Ajan penceresinde hata oluştu:[/bold red] {state.get('error_msg')}")
            else:
                console.print("\n[yellow]Ajan beklenmedik şekilde kapandı veya iptal edildi.[/yellow]")
                
        except KeyboardInterrupt:
            # Kullanıcı işlemi iptal etmek istedi
            if 'process' in locals() and process.poll() is None:
                console.print("\n[bold red]İşlem kullanıcı tarafından iptal edildi. Ajan durduruluyor...[/bold red]")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                state["status"] = "aborted"
                # State'i dosyaya yazarak senkronize et
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=4)
            else:
                console.print("\n[bold yellow]Sistemden çıkılıyor...[/bold yellow]")
                break
        except Exception as e:
            console.print(f"\n[bold red]Ana Terminal Hatası:[/bold red] {e}")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        run_worker()
    else:
        run_main()
