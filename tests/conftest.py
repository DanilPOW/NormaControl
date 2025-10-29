import pytest
import multiprocessing
import time
from app import iface  # Gradio интерфейс

@pytest.fixture(scope="session")
def start_gradio():
    proc = multiprocessing.Process(
        target=iface.launch,
        kwargs={"server_name":"127.0.0.1","server_port":7860,"prevent_thread_lock": True}
    )
    proc.start()
    time.sleep(5)  # ждём пока сервис стартанет
    yield "http://127.0.0.1:7860"
    proc.terminate()
