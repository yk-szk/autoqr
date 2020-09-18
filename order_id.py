import platform
import datetime
import time
import logging
from queue import Queue
from threading import Thread, Event, Lock
from pathlib import Path
from logzero import logger
import pandas as pd

from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout
from PyQt5.QtWidgets import QLabel, QPushButton, QGroupBox, QFileDialog, QLineEdit, QErrorMessage
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTimer

from widgets import VLine, ClockLabel, TimeEdit
from hm_clock import HMClock
import qr

app = QApplication([])
app.setStyle('Fusion')

if platform.system() == 'Windows':
    font = QFont("Courier New", pointSize=10)
    font.setStyleHint(QFont.Monospace)
    app.setFont(font)

if platform.system() == 'Darwin':
    font = QFont("Osaka", pointSize=12)
    font.setStyleHint(QFont.Monospace)
    app.setFont(font)

MSG_DURATION = 2000
N_THREADS = 2
START_INTERVAL = 2  # sec

logger.setLevel(logging.DEBUG)

table_lock = Lock()


def job(PatientID: str, AccessionNumber: str, outdir: str, return_handler,
        error_handler):
    start = datetime.datetime.now()
    logger.info('start %s %s', PatientID, AccessionNumber)
    try:
        new_pid, new_an = qr.qr_anonymize_save(PatientID,
                                               AccessionNumber,
                                               outdir,
                                               logger=logger)
    except Exception as e:
        logger.error('%s', e)
        error_handler(PatientID, AccessionNumber, e)
        return
    return_handler(PatientID, new_pid, AccessionNumber, new_an,
                   datetime.datetime.now() - start)
    logger.info('end %s %s', PatientID, AccessionNumber)


def worker(f, q: Queue, e: Event):
    while True:
        e.wait()
        args = q.get()
        f(*args)
        q.task_done()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.df = None
        self.threads = []
        self.events = []
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.timeout.connect(self.start_workers)
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self.stop_workers_w_start_timer)
        self.task_queue = Queue()
        self.table_filename = 'table.csv'
        self.error_filename = 'errors.txt'
        self.done_count = 0
        self.t_deltas = []
        self.config_widgets = [
        ]  # widgets used for configuration. disabled during the execution
        self._init_widgets()

        for _ in range(N_THREADS):
            e = Event()
            e.clear()
            self.events.append(e)
            t = Thread(target=worker, args=(job, self.task_queue, e))
            t.setDaemon(True)
            self.threads.append(t)
            t.start()

    def is_in_time(self):
        start = HMClock.from_str(self.start_time.text())
        stop = HMClock.from_str(self.stop_time.text())
        now = HMClock.now()

        return now.is_between(start, stop)

    def on_period_change(self, _: str):
        if self.is_in_time():
            self.period_label.setText('実行時間内')
        else:
            self.period_label.setText('実行時間外')

    def _init_periods(self):
        period_gropu = QGroupBox('開始・終了時間')
        period_gropu.setLayout(QHBoxLayout())
        period_gropu.layout().addWidget(QLabel('Start', self))
        self.start_time = TimeEdit()
        self.start_time.setText('1900')
        self.config_widgets.append(self.start_time)
        period_gropu.layout().addWidget(self.start_time)
        period_gropu.layout().addWidget(QLabel('Stop', self))
        self.stop_time = TimeEdit()
        self.start_time.textChanged.connect(self.on_period_change)
        self.stop_time.textChanged.connect(self.on_period_change)
        self.stop_time.setText('0700')
        self.config_widgets.append(self.stop_time)
        period_gropu.layout().addWidget(self.stop_time)

        self.layout.addWidget(period_gropu)

    def _init_output(self):
        def on_browse_button_clicked():
            fileName = QFileDialog.getExistingDirectory(self, '出力先フォルダを選択')
            if fileName != '':
                self.output_edit.setText(fileName)
                logger.info('Set output directory:%s', fileName)
                self.update_button_state()

        output_group = QGroupBox('出力フォルダ')
        output_group.setLayout(QHBoxLayout())
        self.output_edit = QLineEdit()
        self.output_edit.setEnabled(False)
        self.output_edit.setText(
            str(Path.home() / 'Desktop' /
                datetime.date.today().strftime('%m%d')))
        output_group.layout().addWidget(self.output_edit)
        output_button = QPushButton('選択...')
        output_button.clicked.connect(on_browse_button_clicked)
        self.config_widgets.append(output_button)
        output_group.layout().addWidget(output_button)

        self.layout.addWidget(output_group)

    def _handle_result(self, original_pid, new_pid, original_an, new_an,
                       t_delta):
        table_lock.acquire()
        with open(self.table_filename, 'a') as f:
            f.write('{},{},{},{}\n'.format(original_pid, new_pid, original_an,
                                           new_an))
        self.done_count += 1
        self.t_deltas.append(t_delta)
        mean_t_deltas = sum(self.t_deltas, datetime.timedelta()) / len(
            self.t_deltas)
        rate = 1 / (mean_t_deltas.total_seconds() / 3600) * N_THREADS
        self.log_label.setText('{} 完了. {:g} / h'.format(self.done_count, rate))
        table_lock.release()
        if self.done_count == len(self.df):
            logger.info('all jobs are finished')
            self.statusBar().showMessage('全例終了')
            self.stop_workers()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            for w in self.config_widgets:
                w.setEnabled(True)

    def _handle_error(self, PatientID, AccessionNumber, e):
        table_lock.acquire()
        with open(self.error_filename, 'a') as f:
            f.write('{} {} {}\n'.format(PatientID, AccessionNumber, e))
        self.done_count += 1
        table_lock.release()

    def _init_input(self):
        def on_input_button_clicked():
            fileName, _ = QFileDialog.getOpenFileName(self, 'リストを開く', '',
                                                      'CSV File (*.csv)')
            if fileName == '':
                return

            try:
                logger.info('Open input:%s', fileName)
                self.df = pd.read_csv(fileName, encoding='cp932')
                required_cols = ['オーダー番号', '受診者ID', '検査日(yyyy/MM/dd HH:mm)']
                for c in required_cols:
                    if c not in self.df.columns:
                        raise Exception('{}がありません。'.format(c))
                self.df['datetime'] = self.df['検査日(yyyy/MM/dd HH:mm)'].map(
                    lambda d: datetime.datetime.strptime(d, '%Y/%m/%d %H:%M'))
                min_date, max_date = min(self.df['datetime']), max(
                    self.df['datetime'])
                self.input_label.setText('ファイル名：{}、総数：{}\n期間：{} ~ {}'.format(
                    Path(fileName).name, len(self.df),
                    min_date.date().strftime('%Y/%m/%d'),
                    max_date.date().strftime('%Y/%m/%d')))

                self.done_count = 0
                self.t_deltas = []
                self.task_queue.queue.clear()
                for pid, oid in zip(self.df['受診者ID'], self.df['オーダー番号']):
                    self.task_queue.put([
                        pid, oid,
                        self.output_edit.text(), self._handle_result,
                        self._handle_error
                    ])
                self.update_button_state()
            except Exception as e:
                logger.error(e)
                dialog = QErrorMessage(self)
                dialog.setWindowTitle('読み込みエラー')
                dialog.showMessage('無効なファイルです。{}'.format(str(e)))

            self.statusBar().showMessage('リストの読み込み完了')

        input_group = QGroupBox('患者リスト')
        input_group.setLayout(QVBoxLayout())
        input_button = QPushButton('リストを開く')
        input_button.clicked.connect(on_input_button_clicked)
        self.config_widgets.append(input_button)
        input_group.layout().addWidget(input_button)
        self.input_label = QLabel('リストがありません', self)
        self.input_label.setAlignment(Qt.AlignCenter)
        input_group.layout().addWidget(self.input_label)

        self.layout.addWidget(input_group)

    def _init_status(self):
        group = QGroupBox('経過')
        group.setLayout(QHBoxLayout())

        self.log_label = QLabel('0 完了')
        self.log_label.setAlignment(Qt.AlignCenter)
        group.layout().addWidget(self.log_label)
        self.layout.addWidget(group)

    def start_workers(self):
        logger.debug('start_workers')
        self.statusBar().showMessage('Starting workers', MSG_DURATION)
        stop = HMClock.from_str(self.stop_time.text())
        stop_wait = stop - HMClock.now()
        self.stop_timer.start(stop_wait.to_msec() -
                              datetime.datetime.now().second * 1000)
        logger.info('stop in %dh %dm at %s', stop_wait.hour, stop_wait.minute,
                    stop)
        for e in self.events:
            e.set()
            time.sleep(START_INTERVAL)

    def set_start_timer(self):
        start = HMClock.from_str(self.start_time.text())
        wait = start - HMClock.now()
        self.start_timer.start(wait.to_msec() -
                               datetime.datetime.now().second * 1000)
        logger.info('start in %dh %dm at %s', wait.hour, wait.minute, start)
        self.statusBar().showMessage('Scheduled to start at {}'.format(
            HMClock.from_str(self.start_time.text())))

    def stop_workers(self):
        logger.debug('stop_workers')
        for e in self.events:
            e.clear()

    def stop_workers_w_start_timer(self):
        logger.debug('stop_workers_w_start_timer')
        self.set_start_timer()
        for e in self.events:
            e.clear()

    def _init_buttons(self):
        self.stop_button = QPushButton('Stop')
        self.stop_button.setEnabled(False)
        self.start_button = QPushButton('Start')
        self.start_button.setEnabled(False)

        def on_start_button_clicked():
            logger.debug('start button clicked')
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            for w in self.config_widgets:
                w.setEnabled(False)

            output_dir = Path(self.output_edit.text())
            output_dir.mkdir(parents=True, exist_ok=True)
            self.table_filename = output_dir / (
                datetime.datetime.today().strftime("%y%m%d_%H%M%S") + '.csv')
            self.error_filename = output_dir / (
                datetime.datetime.today().strftime("%y%m%d_%H%M%S") +
                '_errors.txt')

            if self.is_in_time():
                self.start_workers()
            else:
                self.set_start_timer()

        def on_stop_button_clicked():
            logger.debug('stop button clicked')
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            for w in self.config_widgets:
                w.setEnabled(True)

            self.statusBar().showMessage('Stopping workers', MSG_DURATION)
            self.start_timer.stop()
            self.stop_timer.stop()
            self.stop_workers()

        self.stop_button.clicked.connect(on_stop_button_clicked)
        self.start_button.clicked.connect(on_start_button_clicked)

        self.layout.addStretch()

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.stop_button)
        bottom_layout.addWidget(self.start_button)

        self.layout.addLayout(bottom_layout)

    def _init_widgets(self):
        central = QWidget(self)
        self.layout = QVBoxLayout()
        central.setLayout(self.layout)
        self.setCentralWidget(central)
        self.setWindowTitle('Auto Q/R')
        self.setMinimumSize(512, 512)

        self.statusBar().setStyleSheet(
            'color: black;background-color: #FFF8DC;')
        self.statusBar().showMessage('App started.', MSG_DURATION)
        self.statusBar().addPermanentWidget(VLine())
        self.period_label = QLabel()
        self.statusBar().addPermanentWidget(self.period_label)
        self.statusBar().addPermanentWidget(VLine())
        self.statusBar().addPermanentWidget(ClockLabel(self))

        self._init_periods()
        self._init_output()
        self._init_input()
        self._init_status()
        self._init_buttons()

    def update_button_state(self):
        def is_ready():
            if self.output_edit.text() == '':
                return False

            if self.df is None:
                return False

            return True

        if is_ready():
            self.start_button.setEnabled(True)


def main():
    window = MainWindow()
    window.show()
    app.exec_()


if __name__ == '__main__':
    main()
