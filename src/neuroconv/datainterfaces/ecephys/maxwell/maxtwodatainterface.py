import os
from pathlib import Path

from pydantic import DirectoryPath, FilePath

from neuroconv.basedatainterface import BaseDataInterface
from neuroconv.tools.spikeinterface import add_recording_to_nwbfile
from neuroconv.utils import DeepDict


class MaxTwoRecordingInterface(BaseDataInterface):  # pragma: no cover
    """
    Primary data interface class for converting all wells/streams from MaxTwo data.

    Uses the :py:class:`~spikeinterface.extractors.MaxwellRecordingExtractor` from SpikeInterface.
    This interface natively discovers all active streams in the file and extracts them together.
    """

    display_name = "MaxTwo Recording"
    associated_suffixes = (".raw", ".h5")
    info = "Interface for MaxTwo recording data (handles all multi-well streams simultaneously)."

    @classmethod
    def get_source_schema(cls) -> dict:
        return {
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the MaxTwo .raw.h5 file."
                }
            },
            "required": ["file_path"],
        }

    @staticmethod
    def auto_install_maxwell_hdf5_compression_plugin(
        hdf5_plugin_path: DirectoryPath | None = None, download_plugin: bool = True
    ) -> None:
        """
        If you do not yet have the Maxwell compression plugin installed, this function will automatically install it.
        """
        from neo.rawio.maxwellrawio import auto_install_maxwell_hdf5_compression_plugin

        auto_install_maxwell_hdf5_compression_plugin(hdf5_plugin_path=hdf5_plugin_path, force_download=download_plugin)

    def __init__(
        self,
        file_path: FilePath,
        *,
        hdf5_plugin_path: DirectoryPath | None = None,
        download_plugin: bool = True,
        verbose: bool = False,
    ) -> None:
        """
        Load and prepare data for all active wells from a MaxTwo file.

        Parameters
        ----------
        file_path : string or Path
            Path to the .raw.h5 file.
        hdf5_plugin_path : string or Path, optional
            Path to your systems HDF5 plugin library.
            Uses the home directory by default.
        download_plugin : boolean, default: True
            Whether to force download of the decompression plugin.
        verbose : boolean, default: False
            Allows verbosity.
        """
        # BaseDataInterface initialization
        super().__init__(file_path=file_path, verbose=verbose)
        self.file_path = file_path

        # Install decompression plugin
        hdf5_plugin_path = os.environ.get(
            "HDF5_PLUGIN_PATH",
            hdf5_plugin_path or Path.home() / "hdf5_plugin_path_maxwell",
        )
        os.environ["HDF5_PLUGIN_PATH"] = str(hdf5_plugin_path)

        if download_plugin:
            self.auto_install_maxwell_hdf5_compression_plugin(hdf5_plugin_path=hdf5_plugin_path)

        # Local import to prevent global SpikeInterface loading
        from spikeinterface.extractors.extractor_classes import MaxwellRecordingExtractor

        # Discover all streams (wells) in the dataset
        stream_names, stream_ids = MaxwellRecordingExtractor.get_streams(file_path=self.file_path)

        # Initialize a dictionary to hold an extractor for every stream
        self.recording_extractors = {}
        for stream_id in stream_ids:
            self.recording_extractors[stream_id] = MaxwellRecordingExtractor(
                file_path=self.file_path, stream_id=stream_id
            )

    def get_metadata(self) -> DeepDict:
        metadata = super().get_metadata()

        metadata.setdefault("Ecephys", dict())
        metadata["Ecephys"].setdefault("Device", [])
        
        # Pull Maxwell version from the first available stream
        first_extractor = list(self.recording_extractors.values())[0]
        maxwell_version = first_extractor.neo_reader.raw_annotations["blocks"][0]["maxwell_version"]

        metadata["Ecephys"]["Device"].append(
            dict(
                name="MaxTwo",
                description=f"Maxwell Biosystems MaxTwo HD-MEA. Recorded using Maxwell version '{maxwell_version}'.",
                manufacturer="Maxwell Biosystems"
            )
        )

        metadata["Ecephys"].setdefault("ElectrodeGroup", [])
        for stream_id in self.recording_extractors.keys():
            metadata["Ecephys"]["ElectrodeGroup"].append(
                dict(
                    name=f"ElectrodeGroup_{stream_id}",
                    description=f"HD-MEA for stream {stream_id}",
                    location="primary neuron culture",
                    device="MaxTwo"
                )
            )

        return metadata

    def add_to_nwbfile(self, nwbfile, metadata: dict | None = None, **kwargs):
        """
        Iterate over all SpikeInterface extractors and add them as separate ElectricalSeries to the NWBFile.
        """
        if metadata is None:
            metadata = self.get_metadata()

        for stream_id, recording_extractor in self.recording_extractors.items():
            if self.verbose:
                print(f"Writing stream {stream_id} to NWB...")

            # Pass the individual recording extractor to the NeuroConv writing tool
            add_recording_to_nwbfile(
                recording=recording_extractor,
                nwbfile=nwbfile,
                metadata=metadata,
                es_key=f"ElectricalSeries_{stream_id}",
                write_as="raw",
                iterator_type="v2",
                write_electrical_series=True,
                **kwargs
            )
