"""Local TCP byte-stream emulator exercising the actual pyserial adapter.

No COM driver, physical wire, FPGA or board correctness is asserted here.
"""
import socket
from threading import Event, Thread
from protocol.codec import Decoder, encode
from simulator.device import SimulatedDevice


class SerialEmulator:
    def __init__(self, device=None, transform=None, line_baud=None):
        self.device = device or SimulatedDevice()
        self.transform = transform or (lambda frame: frame)
        self.line_baud = line_baud
        self.stop = Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(.1)
        self.url = f"socket://127.0.0.1:{self.listener.getsockname()[1]}"
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                peer, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with peer:
                peer.settimeout(.1)
                decoder = Decoder()
                while not self.stop.is_set():
                    try:
                        data = peer.recv(4096)
                        if not data:
                            break
                        for request in decoder.feed(data):
                            response = self.transform(self.device.handle(request))
                            if response is None:
                                continue
                            raw = encode(response)
                            # Fragment coalesced packets independently of protocol.
                            for start in range(0, len(raw), 128):
                                chunk = raw[start:start+128]
                                if self.line_baud:
                                    self.stop.wait(len(chunk) * 10 / self.line_baud)
                                peer.sendall(chunk)
                    except socket.timeout:
                        continue
                    except OSError:
                        break

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
