import argparse,sys
from PySide6.QtWidgets import QApplication
from mvp4.ui import MainWindow
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--port");ap.add_argument("--root");args=ap.parse_args()
 app=QApplication(sys.argv[:1]);window=MainWindow(root=args.root,port=args.port);window.show();return app.exec()
if __name__=="__main__":raise SystemExit(main())

