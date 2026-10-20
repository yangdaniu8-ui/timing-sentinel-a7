"""Bounded pyserial transport. Opening a port does NOT confirm a device."""
import re
import serial
from serial.tools import list_ports


def enumerate_ports():
    return [{"port": p.device, "description": p.description,
             "vid": p.vid, "pid": p.pid, "serial_number": p.serial_number}
            for p in list_ports.comports()]


class SerialTransport:
    kind = "serial"
    origin = "hardware"

    def __init__(self, port, baudrate=115200, *, allow_test_url=False):
        if not (re.fullmatch(r"COM[1-9][0-9]*", port, re.IGNORECASE) or
                (allow_test_url and port.startswith("socket://127.0.0.1:"))):
            raise ValueError("请选择实际COM端口；网络URL仅用于自动测试")
        if baudrate not in (115200, 230400, 460800, 921600):
            raise ValueError("不支持的控制链路波特率")
        self.baudrate = baudrate
        if allow_test_url:
            self.origin = "mock"
            self.kind = "serial_test"
        self.description = f"{port} / {baudrate} / 8N1"
        self.handle = serial.serial_for_url(port, do_not_open=True)
        self.handle.baudrate = baudrate
        self.handle.timeout = 0.02
        self.handle.write_timeout = 0.5
        self.handle.rtscts = self.handle.dsrdtr = self.handle.xonxoff = False
        # Do not deliberately assert reset lines; some USB drivers can still
        # pulse DTR/RTS while opening. Check the board wiring independently.
        self.handle.dtr = self.handle.rts = False
        try:
            self.handle.open()
        except (serial.SerialException, OSError) as exc:
            self.handle.close()
            raise ConnectionError(f"无法打开{port}：{exc}（检查驱动、接线及端口占用）") from exc

    def request_timeout(self, request_bytes, response_bytes):
        # 8N1 => ten line bits per byte, USB latency + FPGA processing margin.
        return max(0.5, (request_bytes + response_bytes) * 10 / self.baudrate + 0.35)

    def write(self, data):
        try:
            if self.handle.write(data) != len(data):
                raise ConnectionError("串口写入不完整，命令结果待确认")
        except (serial.SerialException, OSError) as exc:
            raise ConnectionError(f"串口写入失败：{exc}") from exc

    def read(self, maximum=4096):
        try:
            # A finite timeout batches driver/socket fragments without busy
            # polling. Some URL adapters expose in_waiting as readiness (0/1),
            # not an accurate byte count; reading one byte adds avoidable delay.
            return self.handle.read(maximum)
        except (serial.SerialException, OSError) as exc:
            raise ConnectionError(f"串口读取失败/设备断开：{exc}") from exc

    def close(self):
        self.handle.close()
