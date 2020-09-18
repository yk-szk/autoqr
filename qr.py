from pathlib import Path
import logging
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, build_role
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind
from pynetdicom.sop_class import (PatientRootQueryRetrieveInformationModelGet,
                                  CTImageStorage,
                                  PositronEmissionTomographyImageStorage)

from logzero import setup_logger

import settings
import anonymize
import hash_utils

default_logger = setup_logger()
default_logger.setLevel(logging.DEBUG)

logging.getLogger('pynetdicom').setLevel(logging.WARNING)


def query(ds: Dataset, logger=None):
    logger = logger or default_logger
    logger.debug('start query')
    ae = AE(ae_title=settings.AET)
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(settings.DICOM_SERVER,
                         settings.PORT,
                         ae_title=settings.AEC)
    datasets = []

    if not assoc.is_established:
        raise RuntimeError('Association rejected, aborted or never connected')

    responses = assoc.send_c_find(
        ds, PatientRootQueryRetrieveInformationModelFind)
    for (status, identifier) in responses:
        if not status:
            raise RuntimeError(
                'Connection timed out, was aborted or received invalid response'
            )
        if status.Status == 0xFF00:
            datasets.append(identifier)

    assoc.release()
    logger.debug('end query %d', len(datasets))
    return datasets


def retrieve(ds, logger=None):
    logger = logger or default_logger
    logger.debug('start retrieve')

    stored_datasets = []

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta

        stored_datasets.append(ds)  # append is thread safe
        return 0x0000

    handlers = [(evt.EVT_C_STORE, handle_store)]

    ae = AE(ae_title=settings.AET)

    ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
    ae.add_requested_context(CTImageStorage)
    ae.add_requested_context(PositronEmissionTomographyImageStorage)
    role_ct = build_role(CTImageStorage, scp_role=True)
    role_pt = build_role(PositronEmissionTomographyImageStorage, scp_role=True)

    assoc = ae.associate(settings.DICOM_SERVER,
                         settings.PORT,
                         ext_neg=[role_ct, role_pt],
                         evt_handlers=handlers,
                         ae_title=settings.AEC)
    if not assoc.is_established:
        raise RuntimeError('Association rejected, aborted or never connected')

    responses = assoc.send_c_get(ds,
                                 PatientRootQueryRetrieveInformationModelGet)
    for (status, _) in responses:
        if not status:
            raise RuntimeError(
                'Connection timed out, was aborted or received invalid response'
            )

    assoc.release()
    logger.debug('end retrieve %d', len(stored_datasets))
    return stored_datasets


def qr(ds: Dataset, predicate=None, logger=None):
    logger = logger or default_logger
    found_datasets = query(ds, logger)
    if predicate is not None:
        found_datasets = [ds for ds in found_datasets if predicate(ds)]

    all_datasets = []
    for found_ds in found_datasets:
        ds = Dataset()
        ds.QueryRetrieveLevel = 'SERIES'
        ds.PatientID = found_ds.PatientID
        ds.AccessionNumber = found_ds.AccessionNumber
        ds.StudyInstanceUID = found_ds.StudyInstanceUID
        ds.SeriesInstanceUID = found_ds.SeriesInstanceUID
        datasets = retrieve(ds, logger)
        all_datasets.append(datasets)

    return all_datasets


def qr_anonymize_save(PatientID: str,
                      AccessionNumber: str,
                      outdir: str,
                      logger=None):
    '''
    Q/R and save
    '''
    logger = logger or default_logger
    ds = Dataset()
    ds.PatientID = PatientID
    ds.StudyInstanceUID = ''
    ds.SeriesInstanceUID = ''
    ds.QueryRetrieveLevel = 'SERIES'
    ds.Modality = ''
    ds.AccessionNumber = AccessionNumber
    ds.SeriesDescription = ''

    all_datasets = qr(ds)

    zip_root = Path(outdir)

    for datasets in all_datasets:
        dcm = datasets[0]
        year, date = dcm.StudyDate[:4], dcm.StudyDate[4:]
        new_pid = hash_utils.hash_id(dcm.PatientID)
        new_study_uid = anonymize.anonymize_study_uid(dcm)
        new_series_uid = anonymize.anonymize_series_uid(dcm)
        zipdir = zip_root / year / date / new_pid / new_study_uid
        zipdir.mkdir(parents=True, exist_ok=True)
        zip_filename = anonymize.get_available_filename(
            str(zipdir / new_series_uid), '.zip')

        anonymize.anonymize_dcm(datasets, str(zip_filename))
    return new_pid


def main():
    ds = Dataset()
    ds.PatientID = '2Omc-Ajo60NctUzJpd4Q8w'
    ds.StudyInstanceUID = ''
    ds.SeriesInstanceUID = ''
    ds.QueryRetrieveLevel = 'SERIES'
    ds.Modality = ''
    ds.AccessionNumber = 'Ci5Lj86Rg4HrLuRLZjnqAA'
    ds.SeriesDescription = ''

    all_datasets = qr(ds)
    print(len(all_datasets))


if __name__ == "__main__":
    main()
