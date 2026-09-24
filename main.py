# Copyright (c) 2023-2026 JunSu - AI
# Released under the MIT License.
# See LICENSE file for full license text.


"""
whiteBoxRAG Startup Script
White-box private RAG system with a built-in RAG debugger

Features：
- Detect Python version and virtual environment automatically
- Check Ollama service status
- Create necessary storage directories
- Install dependencies (first run)
- Start FastAPI service

Environment and dependencies are managed by uv (https://docs.astral.sh/uv/):
`--install` runs `uv sync --locked`, which creates the `.venv` environment and installs exactly
what `uv.lock` pins. The project itself is never installed as a package — the service always runs
from the source tree (`uvicorn api.api:app`).

Usage：
    python main.py                  # Default startup
    python main.py --install        # uv sync first (creates .venv, installs dependencies)
    python main.py --install --dev  # ... and install the dev group (pytest, flake8) as well
    python main.py --dev            # Development mode (auto-reload)
    python main.py --no-check       # Skip environment check and start
"""
import os
import sys
import signal
import subprocess
import shutil
import locale
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


def _utf8_env() -> dict:
    """Environment with UTF-8 stdio, so uv/uvicorn never mangle CJK output on Windows"""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return env


def find_uv() -> Optional[str]:
    """
    Locate the uv executable

    uv owns the virtual environment (`.venv`) and the locked dependency set, so it is the only
    prerequisite of the install path. PATH is checked first, then the install locations a freshly
    opened shell may not have picked up yet.

    Returns:
        Path to uv, or None when uv is not installed
    """
    uv = shutil.which("uv")
    if uv:
        return uv

    candidates = [
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv.exe",
        Path.home() / ".cargo" / "bin" / "uv",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "uv.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


def print_uv_hint():
    """Explain how to install uv, the only prerequisite of the install path"""
    print_color("[ERROR] uv is not installed, but it manages the .venv environment and dependencies", Colors.RED)
    print_color("Install uv with any one of:", Colors.YELLOW)
    print("    Windows:      winget install --id=astral-sh.uv -e")
    print("    macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("    Any platform: pip install uv")
    print_color("Then re-run: python main.py --install", Colors.YELLOW)


def project_venv_dir() -> Optional[Path]:
    """
    Return the project virtual environment directory

    uv creates `.venv`; a legacy `venv` directory is still honoured so that an environment built
    by an older checkout (or by hand) keeps working.

    Returns:
        The environment directory, or None when the project has none yet
    """
    for name in (".venv", "venv"):
        venv_dir = Path(__file__).parent / name
        if venv_dir.is_dir():
            return venv_dir
    return None


def sync_dependencies(with_dev: bool = False) -> bool:
    """
    Create or refresh the project environment from the committed lockfile

    Equivalent to `uv sync --locked` (plus `--no-dev` unless development dependencies are
    wanted): uv creates `.venv` when it is missing and installs exactly what `uv.lock` pins.

    Args:
        with_dev: also install the `dev` dependency group (pytest, flake8)

    Returns:
        Whether the environment is ready
    """
    root = Path(__file__).parent

    uv = find_uv()
    if not uv:
        print_uv_hint()
        return False

    if not (root / "pyproject.toml").is_file() or not (root / "uv.lock").is_file():
        print_color("[ERROR] pyproject.toml / uv.lock not found - cannot resolve dependencies", Colors.RED)
        return False

    args = [uv, "sync", "--locked"]
    if not with_dev:
        args.append("--no-dev")

    print_color(
        f"[INSTALL] uv sync ({'runtime + dev' if with_dev else 'runtime'} dependencies)...",
        Colors.BLUE
    )

    try:
        # uv reports its own progress: stream it instead of hiding it behind capture_output
        completed = subprocess.run(args, cwd=str(root), env=_utf8_env())
    except OSError as e:
        print_color(f"[ERROR] Failed to run uv: {e}", Colors.RED)
        return False

    if completed.returncode != 0:
        print_color("[ERROR] uv sync failed", Colors.RED)
        print_color("If pyproject.toml changed, refresh the lockfile with: uv lock", Colors.YELLOW)
        return False

    print_color("[SUCCESS] Dependencies installed ✓", Colors.GREEN)
    return True



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

    # Check if the uv environment (.venv) or a legacy manual venv/ exists
    venv_dir = project_venv_dir()
    if venv_dir:
        print_color(f"[CHECK] Virtual environment directory found: {venv_dir}", Colors.YELLOW)
        print_color("[SUGGESTION] Please activate the virtual environment:", Colors.YELLOW)

        # Windows and Linux/macOS activation commands are different
        if sys.platform == 'win32':
            print(f"    Windows CMD:        {venv_dir.name}\\Scripts\\activate.bat")
            print(f"    Windows PowerShell: {venv_dir.name}\\Scripts\\Activate.ps1")
            print(f"    Or run directly:    {venv_dir.name}\\Scripts\\python.exe main.py")
        else:
            print(f"    Linux/macOS:   source {venv_dir.name}/bin/activate")
            print(f"    Or run directly:    {venv_dir.name}/bin/python main.py")
        print("    With uv:            uv run python main.py")

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


# Known provider identifiers. Used for validation and routing.
_SUPPORTED_LLM_PROVIDERS = ('ollama', 'openai')
_SUPPORTED_EMBEDDING_PROVIDERS = ('ollama', 'openai', 'local')


def check_llm_provider() -> bool:
    """
    Validate the configured LLM provider (LLM only, not embedding).

    Reads llm.provider from config and validates that the chosen backend
    (ollama or openai) has the minimum required LLM-side configuration.

    Returns:
        True if the LLM provider looks properly configured, False otherwise.
    """
    from config import config

    provider = str(config.get('llm.provider', '')).strip().lower()

    if provider not in _SUPPORTED_LLM_PROVIDERS:
        print_color(
            f"[ERROR] Unknown LLM_PROVIDER='{provider}'. Must be one of: {', '.join(_SUPPORTED_LLM_PROVIDERS)}",
            Colors.RED
        )
        print_color("[SUGGESTION] Set LLM_PROVIDER in your .env file", Colors.YELLOW)
        print("    LLM_PROVIDER=ollama   # local Ollama service")
        print("    LLM_PROVIDER=openai   # OpenAI-compatible API")
        return False

    if provider == 'openai':
        return _check_openai_llm(config)
    return _check_ollama_llm(config)


def check_embedding_provider() -> bool:
    """
    Validate the configured embedding provider.

    Embedding is independent from the LLM provider — if EMBEDDING_PROVIDER is
    left blank we fall back to whatever LLM_PROVIDER is. Three backends are
    supported: ollama, openai, local (HuggingFace).

    Returns:
        True if the embedding provider looks properly configured, False otherwise.
    """
    from config import config

    provider = str(config.get('embedding.provider', '')).strip().lower()
    if not provider:
        # Fall back to LLM provider when EMBEDDING_PROVIDER is blank
        provider = str(config.get('llm.provider', '')).strip().lower()

    if provider not in _SUPPORTED_EMBEDDING_PROVIDERS:
        print_color(
            f"[ERROR] Unknown EMBEDDING_PROVIDER='{provider}'. Must be one of: {', '.join(_SUPPORTED_EMBEDDING_PROVIDERS)}",
            Colors.RED
        )
        print_color("[SUGGESTION] Set EMBEDDING_PROVIDER in your .env file (or leave blank to match LLM_PROVIDER)", Colors.YELLOW)
        return False

    print_color(f"[CHECK] Embedding provider ({provider})...", Colors.BLUE)

    if provider == 'local':
        model_path = config.get('embedding.local_model_path', '')
        if not model_path:
            print_color(
                "[ERROR] EMBEDDING_PROVIDER=local but LOCAL_EMBEDDING_MODEL_PATH is not set",
                Colors.RED
            )
            print_color(
                "[SUGGESTION] Set LOCAL_EMBEDDING_MODEL_PATH in your .env file",
                Colors.YELLOW
            )
            print("    # Example (local path):")
            print("    LOCAL_EMBEDDING_MODEL_PATH=D:\\models\\bge-large-zh-v1.5")
            print("    # Example (HF model name):")
            print("    LOCAL_EMBEDDING_MODEL_PATH=BAAI/bge-large-zh-v1.5")
            return False
        print_color(
            f"[Success] Local embedding configured — model: {model_path} ✓",
            Colors.GREEN
        )
        return True

    if provider == 'openai':
        api_key = config.get('openai.api_key', '')
        embed_model = config.get('openai.embedding_model', '')
        missing = []
        if not api_key:
            missing.append('OPENAI_API_KEY')
        if not embed_model:
            missing.append('OPENAI_EMBEDDING_MODEL')
        if missing:
            print_color(
                f"[ERROR] OpenAI embedding config incomplete — missing: {', '.join(missing)}",
                Colors.RED
            )
            return False
        print_color(
            f"[Success] OpenAI embedding configured — model: {embed_model} ✓",
            Colors.GREEN
        )
        return True

    # Ollama embedding
    embed_model = config.get('ollama.embedding_model', '')
    if not embed_model:
        print_color(
            "[ERROR] EMBEDDING_PROVIDER=ollama but OLLAMA_EMBEDDING_MODEL is not set",
            Colors.RED
        )
        print_color("[SUGGESTION] Uncomment and set OLLAMA_EMBEDDING_MODEL in your .env file", Colors.YELLOW)
        return False
    print_color(
        f"[Success] Ollama embedding configured — model: {embed_model} ✓",
        Colors.GREEN
    )
    return True


def _check_ollama_llm(config) -> bool:
    """Validate Ollama LLM configuration and service reachability.

    This only checks LLM-side fields (base_url + llm_model). Embedding fields
    are validated independently by check_embedding_provider(), because the
    embedding backend is now decoupled from the LLM backend.
    """
    print_color("[CHECK] Ollama LLM provider...", Colors.BLUE)

    import urllib.request
    import urllib.error

    # --- Phase 1: sanity-check that the required LLM fields are non-empty ---
    base_url = config.get('ollama.llm_base_url', '')
    llm_model = config.get('ollama.llm_model', '')

    missing_cfg = []
    if not base_url:
        missing_cfg.append('OLLAMA_BASE_URL')
    if not llm_model:
        missing_cfg.append('OLLAMA_LLM_MODEL')

    if missing_cfg:
        print_color(
            f"[ERROR] Ollama LLM config is incomplete — missing: {', '.join(missing_cfg)}",
            Colors.RED
        )
        print_color("[SUGGESTION] Set them in your .env file", Colors.YELLOW)
        return False

    base_url = base_url.rstrip('/')
    ollama_url = f"{base_url}/api/tags"

    # --- Phase 2: check Ollama service connectivity ---
    try:
        req = urllib.request.Request(ollama_url)
        with urllib.request.urlopen(req, timeout=3) as response:
            data = response.read().decode('utf-8')
    except urllib.error.URLError:
        print_color(f"[ERROR] Ollama service is not reachable at {base_url}", Colors.RED)
        print_color("[SUGGESTION] Start Ollama, or correct OLLAMA_BASE_URL in .env", Colors.YELLOW)
        print("    ollama serve")
        return False
    except Exception as e:
        print_color(f"[ERROR] Ollama check failed: {e}", Colors.RED)
        return False

    # --- Phase 3: check that the required LLM model is installed ---
    import json
    models_data = json.loads(data)
    models = [m.get('name', '') for m in models_data.get('models', [])]

    if len(models) == 0:
        print_color("[Warning] Ollama service is running but no models detected", Colors.YELLOW)
        print_color("[Suggest] Download the required LLM model first:", Colors.YELLOW)
        print(f"    ollama pull {llm_model}")
        return True  # service is up, models just need pulling

    if llm_model not in models:
        print_color(
            f"[Warning] Ollama is running but missing LLM model: {llm_model}",
            Colors.YELLOW
        )
        print_color("[Suggest] Pull it with:", Colors.YELLOW)
        print(f"    ollama pull {llm_model}")
    else:
        print_color(
            f"[Success] Ollama LLM ready — service at {base_url}, model '{llm_model}' found ✓",
            Colors.GREEN
        )

    return True


def _check_openai_llm(config) -> bool:
    """Validate OpenAI LLM configuration (api_key + base_url + llm_model).

    Embedding model is NOT checked here — that belongs to check_embedding_provider()
    because the embedding backend is now independent from the LLM backend.
    """
    print_color("[CHECK] OpenAI LLM provider configuration...", Colors.BLUE)

    api_key = config.get('openai.api_key', '')
    base_url = config.get('openai.base_url', '')
    llm_model = config.get('openai.llm_model', '')

    missing_cfg = []
    if not api_key:
        missing_cfg.append('OPENAI_API_KEY')
    if not base_url:
        missing_cfg.append('OPENAI_BASE_URL')
    if not llm_model:
        missing_cfg.append('OPENAI_LLM_MODEL')

    if missing_cfg:
        print_color(
            f"[ERROR] OpenAI LLM config is incomplete — missing: {', '.join(missing_cfg)}",
            Colors.RED
        )
        print_color("[SUGGESTION] Set these in your .env file", Colors.YELLOW)
        for key in missing_cfg:
            print(f"    # edit .env and set: {key}=<your-value>")
        return False

    print_color(
        f"[Success] OpenAI LLM configured (base={base_url}, llm={llm_model}) ✓",
        Colors.GREEN
    )
    return True


def _kill_port_process(port: int):
    """
    Forcefully kill the process using the specified port

    Args:
        port: Port number
    """
    if sys.platform == 'win32':
        try:
            import subprocess as sp
            # netstat prints in the Windows OEM code page. Read raw bytes and decode leniently so
            # that neither a non-UTF-8 console nor PYTHONUTF8=1 in this process can break the
            # parsing below; the fields we match (`:PORT`, `LISTENING`, the PID) are pure ASCII.
            result = sp.run(
                ['netstat', '-ano'],
                capture_output=True
            )
            output = result.stdout.decode(locale.getpreferredencoding(False), errors='replace')
            for line in output.split('\n'):
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


def start_server(host: str = "127.0.0.1", port: int = 8080, dev_mode: bool = False):
    """
    Start FastAPI server with graceful shutdown support.

    On Ctrl+C the handler forwards SIGINT to the uvicorn subprocess so that
    its FastAPI lifespan shutdown branch runs (saves monitor snapshot,
    stops the scheduler and rule-engine flush thread) before the process
    exits. A second Ctrl+C forces an immediate kill.

    Args:
        host: Listening address
        port: Listening port
        dev_mode: Whether to use in development mode (auto-reload)
    """
    print_color("\n[START] Starting whiteBoxRAG server...", Colors.GREEN)
    print_color(f"[CONFIG] Listening address: http://{host}:{port}", Colors.BLUE)
    print_color(f"[DOCS] Listening address: http://{host}:{port}/docs", Colors.BLUE)
    print_color(f"[FRONTEND] Access address: http://{host}:{port}/static/index.html", Colors.BLUE)
    print_color(f"[MODE] {'Development' if dev_mode else 'Production'}", Colors.BLUE)

    print("\n" + "-" * 60)
    print_color("Press Ctrl+C to stop the server", Colors.YELLOW)
    print_color("(second Ctrl+C forces immediate shutdown)", Colors.YELLOW)
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

    # Launch uvicorn as a subprocess so we can control its lifecycle.
    # Do NOT set creationflags that create a new process group — we WANT the
    # console Ctrl+C event to reach uvicorn so its lifespan shutdown runs.
    proc = None
    shutdown_requested = False
    force_killed = False

    def _handle_sigint(signum, frame):
        """Graceful shutdown on first Ctrl+C, force-kill on second."""
        nonlocal shutdown_requested, force_killed
        if shutdown_requested:
            # Second Ctrl+C: abandon grace, kill immediately.
            force_killed = True
            print_color("\n[FORCE] Second Ctrl+C — force killing uvicorn...", Colors.RED)
            try:
                if proc is not None and proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            sys.exit(1)

        shutdown_requested = True
        print_color("\n[STOP] Ctrl+C received, shutting down gracefully...", Colors.YELLOW)
        print_color("       (saving monitor snapshot, stopping scheduler, flushing rule engine)", Colors.YELLOW)

        # Forward SIGINT to the uvicorn subprocess so its lifespan shutdown
        # branch runs (monitor.save_snapshot / stop_scheduler / rule_engine.stop).
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == 'win32':
                    # Windows: os.kill with SIGINT raises a signal event in the
                    # subprocess console, which uvicorn interprets as a shutdown
                    # request just like a real Ctrl+C from the user.
                    os.kill(proc.pid, signal.SIGINT)
                else:
                    proc.send_signal(signal.SIGINT)
            except Exception:
                # Fallback: SIGTERM if SIGINT delivery failed
                try:
                    proc.terminate()
                except Exception:
                    pass

    # Install handler BEFORE spawning the subprocess so it covers the whole run.
    old_handler = signal.signal(signal.SIGINT, _handle_sigint)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m"] + uvicorn_args,
            cwd=str(project_root),
            env=_utf8_env()
        )

        # Block until uvicorn exits (either normally or after receiving SIGINT).
        proc.wait()

    except Exception as e:
        print_color(f"[ERROR] Server startup failed: {e}", Colors.RED)

    finally:
        # Restore the previous signal handler so we don't leak it on exit.
        signal.signal(signal.SIGINT, old_handler)

        if force_killed:
            print_color("[STOP] Process force-killed", Colors.RED)
        elif shutdown_requested:
            print_color("[STOP] Server stopped gracefully", Colors.GREEN)
        elif proc is not None and proc.returncode != 0:
            print_color(f"[STOP] uvicorn exited with code {proc.returncode}", Colors.YELLOW)


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

    # --install first: uv sync creates .venv and installs exactly what uv.lock pins, so the
    # process restarted below finds every import it needs (the old code restarted first and
    # therefore never installed anything).
    if args.install and not sync_dependencies(with_dev=args.dev):
        sys.exit(1)

    # uv creates .venv; a legacy hand-made venv/ is still honoured
    venv_dir = project_venv_dir()

    # If virtual environment exists, restart in virtual environment
    if venv_dir is not None:
        # Get Python path in virtual environment
        if sys.platform == 'win32':
            venv_python = venv_dir / "Scripts" / "python.exe"
        else:
            venv_python = venv_dir / "bin" / "python"

        if not venv_python.exists():
            print_color(f"[ERROR] Virtual environment Python not found: {venv_python}", Colors.RED)
            sys.exit(1)

        print_color(f"[RESTARTING] Restarting in virtual environment ({venv_dir.name})...", Colors.BLUE)

        # Build new command (no --install: the environment is already in sync)
        new_args = []
        if args.dev:
            new_args.append("--dev")
        if args.no_check:
            new_args.append("--no-check")
        new_args.extend(["--host", args.host])
        new_args.extend(["--port", str(args.port)])

        # Restart in virtual environment. No `env` override on purpose: forcing UTF-8 stdio here
        # would leak into the restarted launcher and change how it decodes console output
        # (see _kill_port_process); the server process itself gets the UTF-8 env in start_server().
        subprocess.run([str(venv_python), __file__] + new_args)
        return True

    # No environment yet: point at the uv workflow instead of a hand-made `python -m venv`
    print_color("\n[TIP] No virtual environment yet - uv creates and manages it for you", Colors.YELLOW)
    print_color("You can:", Colors.YELLOW)
    print("    1. Run 'python main.py --install' to uv sync .venv and start the server")
    print("    2. Or do it manually: uv sync")
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
        help="uv sync first (create .venv, install the locked dependencies) and start the server"
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
        default=None,
        help="Listen address. Defaults to HOST in .env, or 0.0.0.0 if unset."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Listen port. Defaults to PORT in .env, or 8080 if unset."
    )

    args = parser.parse_args()

    # Resolve host/port BEFORE venv restart so restarted process inherits the same values.
    # Priority chain: command-line flag > .env (via config injection) > fallback.
    # settings.yaml intentionally no longer carries host/port — they are deployment
    # environment config, not business rules.
    try:
        from config import config as _cfg
        args.host = args.host or _cfg.get('system.host', '0.0.0.0')
        args.port = args.port or _cfg.get('system.port', 8080)
    except Exception:
        args.host = args.host or '0.0.0.0'
        args.port = args.port or 8080

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

        # 2. LLM provider check (chat/completion backend)
        if not check_llm_provider():
            print_color("", Colors.RESET)
            print_color(
                "[ABORT] LLM provider check failed. Fix the errors above, or re-run with --no-check to skip.",
                Colors.RED
            )
            sys.exit(1)

        # 3. Embedding provider check (independent from LLM — can be ollama/openai/local)
        if not check_embedding_provider():
            print_color("", Colors.RESET)
            print_color(
                "[ABORT] Embedding provider check failed. Fix the errors above, or re-run with --no-check to skip.",
                Colors.RED
            )
            sys.exit(1)

    # Create storage directories
    create_storage_dirs()

    # Install dependencies (if specified). Reaching this point means the process already runs
    # inside .venv (run_in_venv_if_needed restarts there), so uv sync targets that environment.
    if args.install and not sync_dependencies(with_dev=args.dev):
        sys.exit(1)

    # Start server
    start_server(args.host, args.port, args.dev)


if __name__ == "__main__":
    main()