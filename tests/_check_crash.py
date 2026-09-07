"""Crash diagnostic — reproduce PAUSED job state without the event loop."""
import faulthandler
import sys

faulthandler.enable()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState  # noqa: E402
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402

w = NovelForgeDesktopWindow()
w.show()
app.processEvents()
print("Window OK")

cs = w._pages["chapter_studio"]
cs._mode = cs.MODE_BOOK_AUTO
cs._auto_started = True

job = DesktopJobRecord(
    job_id="test-1",
    kind="prepare_chapter",
    label="测试",
    project_id="test",
    status=DesktopJobState.PAUSED,
    result={"chapter_number": 1},
)
cs.bind_jobs([job])
app.processEvents()
print("bind_jobs OK")

QTimer.singleShot(2000, app.quit)
print("Starting event loop...")
app.exec()
print("Done")
