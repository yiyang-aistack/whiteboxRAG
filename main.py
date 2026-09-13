"""
whiteBoxRAG Startup Script
White-box private RAG system with a built-in RAG debugger

Features：
- Detect Python version and virtual environment automatically
- Check Ollama service status
- Create necessary storage directories
- Install dependencies (first run)
- Start FastAPI service

Usage：
    python main.py              # Default startup
    python main.py --install    # Install dependencies and start
    python main.py --dev        # Development mode (auto-reload)
    python main.py --no-check   # Skip environment check and start
"""
import os
import sys
import subprocess
import argparse
import time
from pathlib import Path
from typing import Optional

# Color output (Windows compatible)
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_color(msg: str, color: str = Colors.GREEN):
    """Print a message with a specified color"""
    try:
        print(f"{color}{msg}{Colors.RESET}")
    except UnicodeEncodeError:
        ascii_msg = msg.replace('✓', '[OK]').replace('×', '[FAIL]').replace('→', '->')
        print(f"{color}{ascii_msg}{Colors.RESET}")

def print_header():
    """Print startup header information"""
    print("\n" + "=" * 60)
    print_color(" whiteBoxRAG - white-box private RAG with a built-in RAG debugger", Colors.BOLD + Colors.BLUE)
    print_color(" Version: 1.0.0 | Lightweight deployment | 8G memory adaptation", Colors.BLUE)
    print("=" * 60 + "\n")


def check_python_version() -> bool:
    """
    Check Python version (requires 3.12+)

    Returns:
        Whether the version meets the requirement
    """
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    print_color(f"[CHECK] Python version: {version_str}", Colors.BLUE)

    if version.major < 3 or (version.major == 3 and version.minor < 12):
        print_color(f"[ERROR] Python version is too low, requires 3.12+", Colors.RED)
        print_color("Upgrade Python: https://www.python.org/downloads/", Colors.YELLOW)
        return False

    print_color("[Success] Python version meets the requirement ✓", Colors.GREEN)
    return True


def check_virtual_env() -> bool:
    """
    Check if the script is running in a virtual environment

    Returns:
        Whether the virtual environment is active
    """
    # Check VIRTUAL_ENV environment variable if it's set
    in_venv = os.getenv('VIRTUAL_ENV') is not None

    # Check sys.prefix if it's different from the system Python prefix
    in_venv_by_prefix = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )

    is_in_venv = in_venv or in_venv_by_prefix

    if is_in_venv:
        venv_path = os.getenv('VIRTUAL_ENV') or sys.prefix
        print_color(f"[DETECT] Running in virtual environment: {venv_path}", Colors.GREEN)
        return True
    else:
        print_color("[PROMPT] Not running in virtual environment", Colors.YELLOW)

        # Check if venv directory exists
        venv_dir = Path(__file__).parent / "venv"
        if venv_dir.exists():
            print_color(f"[CHECK] Virtual environment directory found: {venv_dir}", Colors.YELLOW)
            print_color("[SUGGESTION] Please activate the virtual environment:", Colors.YELLOW)

            # Windows and Linux/macOS activation commands are different
            if sys.platform == 'win32':
                print("    Windows CMD:   venv\\Scripts\\activate.bat")
                print("    Windows PowerShell: venv\\Scripts\\Activate.ps1")
                print("    Or run directly:    venv\\Scripts\\python.exe main.py")
            else:
                print("    Linux/macOS:   source venv/bin/activate")
                print("    Or run directly:    venv/bin/python main.py")

        return False


def create_virtual_env() -> Optional[Path]:
    """
    Create virtual environment if not exists

    Returns:
        Virtual environment path
    """
    venv_dir = Path(__file__).parent / "venv"

    if venv_dir.exists():
        print_color(f"[]: {venv_dir}", Colors.YELLOW)
        return venv_dir

    print_color(f"[CREATE] Virtual environment...", Colors.BLUE)

    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True
        )
        print_color(f"[SUCCESS] Virtual environment created: {venv_dir}", Colors.GREEN)
        return venv_dir
    except subprocess.CalledProcessError as e:
        print_color(f"[ERROR] Failed to create virtual environment: {e}", Colors.RED)
        return None


def install_dependencies(venv_python: Optional[str] = None):
    """
    Python dependencies

    Args:
        venv_python: Python path in the virtual environment
    """
    requirements_file = Path(__file__).parent / "requirements.txt"

    if not requirements_file.exists():
        print_color("[ERROR] requirements.txt not found", Colors.RED)
        return False

    print_color("[INSTALL] Python dependencies...", Colors.BLUE)

    # Use domestic mirror to speed up installation
    pip_args = ["install", "--upgrade", "pip"]
    install_args = ["install", "-r", str(requirements_file)]

    # If use Tuna mirror to speed up installation
    use_tuna_mirror = True
    if use_tuna_mirror:
        install_args.extend(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])

    python_exe = venv_python or sys.executable

    try:
        # Upgrade pip
        subprocess.run([python_exe, "-m", "pip"] + pip_args, check=True, capture_output=True)

        # Install dependencies
        result = subprocess.run(
            [python_exe, "-m", "pip"] + install_args,
            check=True,
            capture_output=True,
            text=True
        )

        print_color("[SUCCESS] Python dependencies installed ✓", Colors.GREEN)
        return True

    except subprocess.CalledProcessError as e:
        print_color(f"[ERROR] Failed to install Python dependencies: {e.stderr}", Colors.RED)
        return False


def create_storage_dirs():
    """Create storage directories"""
    base_dir = Path(__file__).parent / "storage"

    dirs = [
        "vectordb",
        "documents",
        "logs",
        "tasks",
        "monitor",
        "traces"
    ]

    print_color("[CREATE] Storage directories...", Colors.BLUE)

    for d in dirs:
        dir_path = base_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)

    print_color("[SUCCESS] Storage directories created ✓", Colors.GREEN)


def check_ollama_service() -> bool:
    """
    Check Ollama service status

    Returns:
        Ollama service is running
    """
    print_color("[CHECK] Ollama service status...", Colors.BLUE)

    import urllib.request
    import urllib.error

    ollama_url = "http://localhost:11434/api/tags"

    try:
        req = urllib.request.Request(ollama_url)
        with urllib.request.urlopen(req, timeout=3) as response:
            data = response.read().decode('utf-8')

            # Check model count
            import json
            models_data = json.loads(data)
            models = models_data.get('models', [])

            if len(models) == 0:
                print_color("[Warning] Ollama service is running but no models detected", Colors.YELLOW)
                print_color("[Suggest] Please download models first:", Colors.YELLOW)
                print("    ollama pull qwen2.5:7b")
                print("    ollama pull nomic-embed-text:latest")
            else:   
                print_color(f"[Success] Ollama service is running, installed {len(models)} models ✓", Colors.GREEN)
                for m in models[:3]:
                    print_color(f"    - {m.get('name', 'unknown')}", Colors.BLUE)

            return True

    except urllib.error.URLError:
        print_color("[ERROR] Ollama service is not running", Colors.RED)
        print_color("[SUGGESTION] Please start Ollama first:", Colors.YELLOW)

        if sys.platform == 'win32':
            print("    Windows: run 'ollama serve'")
        else:
            print("    Linux/macOS: run 'ollama serve'")

        return False
    except Exception as e:
        print_color(f"[ERROR] Ollama check failed: {e}", Colors.RED)
        return False


def _kill_port_process(port: int):
    """
    Forcefully kill the process using the specified port

    Args:
        port: Port number
    """
    if sys.platform == 'win32':
        try:
            import subprocess as sp
            result = sp.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True
            )
            for line in result.stdout.split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    pid = parts[-1]
                    try:
                        sp.run(['taskkill', '/PID', pid, '/F'], capture_output=True)
                        print_color(f"[Success] Process using port {port} killed (PID: {pid})", Colors.GREEN)
                    except:
                        pass
        except Exception as e:
            print_color(f"[WARNING] Failed to kill process using port {port}: {e}", Colors.YELLOW)


def start_server(host: str = "0.0.0.0", port: int = 8080, dev_mode: bool = False):
    """
    Start FastAPI server

    Args:
        host: Listening address
        port: Listening port
        dev_mode: Whether to use in development mode (auto-reload)
    """
    print_color("\n[START] Starting whiteBoxRAG server...", Colors.GREEN)
    print_color(f"[CONFIG] Listening address: http://{host}:{port}", Colors.BLUE)
    print_color(f"[FRONTEND] Access address: http://{host}:{port}/static/index.html", Colors.BLUE)
    print_color(f"[MODE] {'Development' if dev_mode else 'Production'}", Colors.BLUE)

    print("\n" + "-" * 60)
    print_color("Press Ctrl+C to stop the server", Colors.YELLOW)
    print("-" * 60 + "\n")

    _kill_port_process(port)

    time.sleep(1)

    project_root = Path(__file__).parent

    uvicorn_args = [
        "uvicorn",
        "api.api:app",
        "--host", host,
        "--port", str(port),
        "--log-level", "info",
    ]

    if dev_mode:
        uvicorn_args.append("--reload")

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    try:
        subprocess.run(
            [sys.executable, "-m"] + uvicorn_args,
            cwd=str(project_root),
            env=env
        )

    except KeyboardInterrupt:
        print_color("\n[STOP] Server stopped successfully", Colors.YELLOW)
    except Exception as e:
        print_color(f"[ERROR] Server startup failed: {e}", Colors.RED)


def run_in_venv_if_needed(args: argparse.Namespace):
    """
    If needed, run the server in a virtual environment

    Args:
        args: Command-line arguments
    """
    # If already in virtual environment, skip check
    if check_virtual_env():
        return False

    # If --no-check is specified, skip check
    if args.no_check:
        print_color("[SKIP] Skipping environment check", Colors.YELLOW)
        return False

    # Find virtual environment directory
    venv_dir = Path(__file__).parent / "venv"

    # If --install is specified, create virtual environment if not exists
    if not venv_dir.exists() and args.install:
        venv_dir = create_virtual_env()
        if not venv_dir:
            print_color("[ERROR] Failed to create virtual environment", Colors.RED)
            sys.exit(1)

    # If virtual environment exists, restart in virtual environment
    if venv_dir.exists():
        # Get Python path in virtual environment
        if sys.platform == 'win32':
            venv_python = venv_dir / "Scripts" / "python.exe"
        else:
            venv_python = venv_dir / "bin" / "python"

        if not venv_python.exists():
            print_color(f"[ERROR] Virtual environment Python not found: {venv_python}", Colors.RED)
            sys.exit(1)

        print_color(f"[RESTARTING] Restarting in virtual environment...", Colors.BLUE)

        # Build new command (remove --install parameter to avoid duplicate installation)
        new_args = []
        if args.dev:
            new_args.append("--dev")
        if args.no_check:
            new_args.append("--no-check")
        new_args.extend(["--host", args.host])
        new_args.extend(["--port", str(args.port)])

        # Restart in virtual environment
        subprocess.run([str(venv_python), __file__] + new_args)
        return True

    # If virtual environment does not exist, prompt user to create it
    print_color("\n[TIP] Suggested running in virtual environment to avoid dependency conflicts", Colors.YELLOW)
    print_color("You can:", Colors.YELLOW)
    print("    1. Run 'python main.py --install' to create virtual environment automatically")
    print("    2. Create manually: python -m venv venv")
    print("    3. Skip check: python main.py --no-check")

    # Continue in system Python environment
    return False


def main():
    """Main function"""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="whiteBoxRAG Startup Script"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install dependencies and start the server"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Development mode (auto-reload)"
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="Skip environment check"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Listen address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Listen port (default: 8080)"
    )

    args = parser.parse_args()

    # Print header information
    print_header()

    # If needed, run the server in a virtual environment
    if run_in_venv_if_needed(args):
        return  # Server restarted in virtual environment, exit current process

    # Environment check
    if not args.no_check:
        # 1. Python version check
        if not check_python_version():
            sys.exit(1)

        # 2. Ollama service check
        if not check_ollama_service():
            print_color("\n[Tip] You can use the --no-check to skip Ollama check", Colors.YELLOW)
            # Do not force exit, allow user to skip
            time.sleep(2)

    # Create storage directories
    create_storage_dirs()

    # Install dependencies (if specified)
    if args.install:
        install_dependencies()

    # Start server
    start_server(args.host, args.port, args.dev)


if __name__ == "__main__":
    main()