from protocol.codec import Decoder, encode
from simulator.device import SimulatedDevice


class MockTransport:
    kind = "mock"
    origin = "mock"
    description = "In-process simulator"

    def __init__(self, device=None, fragment_size=4096):
        self.device = device or SimulatedDevice()
        self.decoder = Decoder()
        self.rx = bytearray()
        self.fragment_size = fragment_size
        self.drop_next = False
        self.corrupt_next = False
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ConnectionError("Transport closed")
        for frame in self.decoder.feed(data):
            raw = encode(self.device.handle(frame))
            if self.drop_next:
                self.drop_next = False
            else:
                if self.corrupt_next:
                    raw = raw[:-1] + bytes([raw[-1] ^ 1])
                    self.corrupt_next = False
                self.rx.extend(raw)

    def read(self, maximum=4096):
        if self.closed:
            raise ConnectionError("Transport closed")
        size = min(maximum, self.fragment_size, len(self.rx))
        data = bytes(self.rx[:size])
        del self.rx[:size]
        return data

    def close(self):
        self.closed = True

    def stimulate(self, scenario, seed):
        return self.device.stimulate(scenario, seed)
