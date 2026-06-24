"""Rate limiter process-global para chamadas HTTP ao Asaas.

O Asaas bloqueia o token inteiro com HTTP 429 quando recebe rajadas. Este
limiter serializa as chamadas e garante um intervalo mínimo entre elas,
transformando rajadas (de qualquer call-site) numa fila pacejada FIFO.
"""
import threading
import time


class RateLimiter:
    def __init__(self, min_interval: float):
        self._min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._last = 0.0  # monotonic da última liberação

    def wait(self) -> None:
        """Bloqueia até passar `min_interval` desde a última liberação.

        Segura o lock durante o sleep: serializa os chamadores numa fila e
        garante a taxa mesmo com threads concorrentes (chamadas vêm de
        `asyncio.to_thread`, então o sleep não bloqueia o event loop).
        """
        if self._min_interval <= 0:
            return
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()
