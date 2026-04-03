import os
from pathlib import Path

from pydantic import DirectoryPath, FilePath

from neuroconv.basedatainterface import BaseDataInterface
from neuroconv.tools.spikeinterface import add_recording_to_nwbfile
from neuroconv.utils import DeepDict


class MaxTwoRecordingInterface(BaseDataInterface):
    display_name = "MaxTwo Recording"
    associated_suffixes = (".raw", ".h5")

    def __init__(
        self,
        file_path: FilePath,
        *,
        well_metadata: dict | None = None,
        hdf5_plugin_path: DirectoryPath | None = None,
        download_plugin: bool = True,
        verbose: bool = False,
    ):
        super().__init__(file_path=file_path, verbose=verbose)
        self.file_path = file_path
        self.verbose = verbose

        # Set HDF5 plugin path
        hdf5_plugin_path = os.environ.get(
            "HDF5_PLUGIN_PATH",
            hdf5_plugin_path or Path.home() / "hdf5_plugin_path_maxwell",
        )
        os.environ["HDF5_PLUGIN_PATH"] = str(hdf5_plugin_path)

        if download_plugin:
            from neo.rawio.maxwellrawio import auto_install_maxwell_hdf5_compression_plugin
            auto_install_maxwell_hdf5_compression_plugin(hdf5_plugin_path=hdf5_plugin_path)

        # Load streams
        from spikeinterface.extractors.extractor_classes import MaxwellRecordingExtractor

        _, stream_ids = MaxwellRecordingExtractor.get_streams(file_path=self.file_path)

        self.recording_extractors = {
            sid: MaxwellRecordingExtractor(file_path=self.file_path, stream_id=sid)
            for sid in stream_ids
        }

        # Per-well metadata
        self.well_metadata = well_metadata or {}

    # ---------------------------------------------------------------------
    # Per-stream metadata
    # ---------------------------------------------------------------------
    def get_metadata_for_stream(self, stream_id) -> DeepDict:
        metadata = super().get_metadata()
        metadata.setdefault("Ecephys", {})

        wm = self.well_metadata.get(stream_id, {})

        genotype = wm.get("genotype", "UNKNOWN")
        sex = wm.get("sex", "U")
        div = wm.get("DIV", "NA")
        treatment = wm.get("treatment", "none")
        chip_id = wm.get("chip_id", "unknown_chip")
        run_id = wm.get("run_id", "run")

        # NWBFile
        metadata["NWBFile"].update(
            session_description=f"HD-MEA recording well {stream_id}",
            identifier=f"{chip_id}_{run_id}_{stream_id}",
            experiment_description=f"Primary cortical neurons | DIV{div} | {treatment}",
        )

        # Subject
        metadata["Subject"] = dict(
            subject_id=f"{chip_id}_{stream_id}",
            species="Mus musculus",
            genotype=genotype,
            sex=sex,
            description=f"Primary cortical culture (well {stream_id})",
        )

        # Device
        first_extractor = list(self.recording_extractors.values())[0]
        maxwell_version = first_extractor.neo_reader.raw_annotations["blocks"][0]["maxwell_version"]

        metadata["Ecephys"]["Device"] = [dict(
            name="MaxTwo",
            description=f"Maxwell HD-MEA (v{maxwell_version})",
            manufacturer="Maxwell Biosystems",
        )]

        # ElectrodeGroup (one per well)
        metadata["Ecephys"]["ElectrodeGroup"] = [dict(
            name=f"ElectrodeGroup_{stream_id}",
            description=f"Well {stream_id} | genotype={genotype} | DIV={div}",
            location="primary cortical culture",
            device="MaxTwo",
        )]

        # ElectricalSeries (required by NeuroConv)
        metadata["Ecephys"]["ElectricalSeries"] = dict(
            name="ElectricalSeries",
            description=f"Raw extracellular recording | well {stream_id}",
        )

        return metadata

    # ---------------------------------------------------------------------
    # Write a single stream
    # ---------------------------------------------------------------------
    def add_to_nwbfile(self, nwbfile, metadata=None, stream_id=None, **kwargs):
        if stream_id is None:
            raise ValueError("stream_id must be provided")

        recording = self.recording_extractors[stream_id]

        add_recording_to_nwbfile(
            recording=recording,
            nwbfile=nwbfile,
            metadata=metadata,
            es_key="ElectricalSeries",
            write_as="raw",
            iterator_type="v2",
            **kwargs
        )

    # ---------------------------------------------------------------------
    # Driver: convert each stream to its own NWB file
    # ---------------------------------------------------------------------
    def run_conversion_per_stream(self, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)

        for stream_id in self.recording_extractors.keys():
            print(f"Converting {stream_id}")

            metadata = self.get_metadata_for_stream(stream_id)
            out_path = output_dir / f"{stream_id}.nwb"

            self.run_conversion(
                nwbfile_path=str(out_path),
                metadata=metadata,
                overwrite=True,
                conversion_options=dict(
                    add_to_nwbfile=dict(stream_id=stream_id)
                ),
            )