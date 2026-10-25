"""Rebuild the reviewed v6 report using repository-relative inputs.

Windows, ReportLab and Pillow are required, as for the preserved v4 renderer.
Use --out with a new filename; the reviewed PDF is never overwritten.
"""
from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[1]


def main():
    renderer = ROOT / "scripts/build_submission_pdf_v4.py"
    spec = importlib.util.spec_from_file_location("report_layout_v4", renderer)
    layout = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(layout)
    layout.SOURCE = ROOT / "report/technical_report_polished.md"

    original_style = layout.ParagraphStyle

    def polished_style(name, **kwargs):
        if name == "Body":
            kwargs.update(leading=18.6, spaceAfter=6, allowWidows=0, allowOrphans=0)
        return original_style(name, **kwargs)

    layout.ParagraphStyle = polished_style
    original_table = layout.Table

    def polished_table(data, *args, **kwargs):
        if len(data) <= 8:
            kwargs["splitByRow"] = 0
        return original_table(data, *args, **kwargs)

    layout.Table = polished_table
    original_doc = layout.BaseDocTemplate

    class PolishedDoc(original_doc):
        def build(self, story, *args, **kwargs):
            starts = {"特性成果", "负载实验：同一计算任务为何出现超期", "FPGA实现、资源优化与验证"}
            organized = []
            for flow in story:
                title = flow.getPlainText() if hasattr(flow, "getPlainText") else None
                if (title == "第四部分 总结" and organized
                        and isinstance(organized[-1], layout.PageBreak)):
                    organized.pop()
                if title in starts:
                    organized.append(layout.PageBreak())
                organized.append(flow)
            return super().build(organized, *args, **kwargs)

    layout.BaseDocTemplate = PolishedDoc
    layout.main()


if __name__ == "__main__":
    main()
