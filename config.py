import sys
from pathlib import Path
import toml
from logzero import logger as default_logger


class Defaults():
    def __init__(self):
        self.DICOM_SERVER = 'localhost'  # DICOM server's IP address or hostname
        self.__PORT = 4242  # DICOM server's port
        self.AEC = 'ANY-SCP'  # DICOM server's AET
        self.AETS = ['AUTOQR']  # Client's application Entity Title
        self.__PERIODS = [['1800', '0700']]
        self.DCMTK_BINDIR = ''
        self.__N_THREADS = 1
        self.__RECEIVE_PORTS = [104]
        self.COL_ACCESSION_NUMBER = 'AccessionNumber'
        self.COL_STUDY_INSTANCE_UID = 'StudyInstanceUID'
        self.COL_STUDY_DATE = 'StudyDate'
        self.COL_PATIENT_ID = 'PatientID'
        self.DATETIME_FORMAT = '%Y%m%d'

    @property
    def N_THREADS(self):
        return self.__N_THREADS

    @N_THREADS.setter
    def N_THREADS(self, n_str: str):
        self.__N_THREADS = int(n_str)

    @property
    def PORT(self):
        return self.__PORT

    @PORT.setter
    def PORT(self, port_str: str):
        self.__PORT = int(port_str)

    @property
    def PERIODS(self):
        return self.__PERIODS

    @PERIODS.setter
    def PERIODS(self, periods):
        for p in periods:
            if len(p) != 2:
                default_logger.error(
                    'Invalid PERIODS in the config. Nested periods is expected (e.g. [["1800", "0600"]]): %s',
                    periods)
                sys.exit(1)
        self.__PERIODS = periods

    @property
    def RECEIVE_PORTS(self):
        return self.__RECEIVE_PORTS

    @RECEIVE_PORTS.setter
    def RECEIVE_PORTS(self, port_str: str):
        self.__RECEIVE_PORTS = [int(e) for e in port_str]

    def load(self, filename, logger=None):
        with open(filename, encoding='utf8') as f:
            config = toml.load(f)
        vs = [v for v in dir(self) if not v.startswith('__')]
        for key in config.keys():
            if key in vs:
                if logger:
                    logger.info('Reset %s = %s', key, config[key])
                setattr(settings, key, config[key])
            elif logger:
                logger.warning('%s is invalid config key', key)


settings = Defaults()
config_filename = Path(__file__).parent / 'config.toml'
if config_filename.exists():
    settings.load(config_filename, default_logger)
