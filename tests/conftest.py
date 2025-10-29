import pytest
import multiprocessing
import time
from app import iface  # Gradio интерфейс

@pytest.fixture(scope="session")
def start_gradio():
    proc = multiprocessing.Process(
        target=iface.launch,
        kwargs={
            "server_name": "127.0.0.1",
            "server_port": 7860,
            "prevent_thread_lock": True,
            "show_error": True,  # Добавьте это
            "quiet": True,       # И это
        }
    )
    proc.start()
    time.sleep(10)  # Увеличьте время ожидания
    # Проверьте, что сервер запустился
    try:
        response = requests.get("http://127.0.0.1:7860/", timeout=5)
        if response.status_code == 200:
            yield "http://127.0.0.1:7860"
        else:
            raise Exception("Gradio не ответил")
    except Exception as e:
        proc.terminate()
        raise e
    finally:
        proc.terminate()
