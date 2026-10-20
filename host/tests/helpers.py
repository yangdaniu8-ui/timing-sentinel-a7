from app.services.device_client import DeviceClient
from simulator.device import SimulatedDevice
from transport.mock import MockTransport


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def setup_device(config=None):
    clock = Clock()
    device = SimulatedDevice(clock)
    transport = MockTransport(device, fragment_size=4096)
    client = DeviceClient(transport, timeout=0.02)
    client.connect()
    if config:
        client.configure(config)
    client.arm()
    assert client.status()["state"] == "PREFILL"
    clock.advance(0.1)
    assert client.status()["ready"]
    return clock, device, transport, client


def frozen_capture(config=None, scenario="normal", delay=None):
    from app.services.capture_reader import read_capture
    clock, device, transport, client = setup_device(config)
    device.stimulate(scenario, delay_ticks=delay)
    clock.advance(.2)
    return read_capture(client), device, client
