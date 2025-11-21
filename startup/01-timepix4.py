#!/usr/bin/env python3
"""
Ophyd Device for Timepix4 detector controlled via HTTP server with proxy functionality

The device communicates with the http-server-receiver which acts as a proxy to the
collection server. The receiver server provides:

Data Acquisition:
- POST /acquire - Start data acquisition with file path and period

Detector Control (Proxy to Collection Server):
- POST /detector/readtemp - Read detector temperature
- POST /detector/reset - Reset the detector
- POST /detector/arm - Arm the detector
- POST /detector/init - Initialize the detector
- POST /detector/acquire - Acquire detector data
- GET /detector/status - Get detector status

Example usage:
    from timepix4_device import Timepix4Detector

    # Initialize detector device (pointing to receiver server)
    det = Timepix4Detector(
        'xf11id1-tpx4:',
        name='timepix4',
        server_host='localhost',  # Receiver server
        server_port=8000
    )

    # Read temperature (uses proxy endpoint)
    temp = det.read_temperature()

    # Use in a Bluesky plan
    from bluesky import RunEngine
    from bluesky.plans import count

    RE = RunEngine({})
    RE(count([det], num=5))

Note: The receiver server must be running and configured to proxy to the collection server.
"""

from ophyd import Device, Component as Cpt, Signal
from ophyd.status import Status
from datetime import datetime
from event_model import compose_resource
from pathlib import Path
from collections import deque
import requests
import time
import threading
import uuid


class ExternalFileReference(Signal):
    """
    A pure software Signal that describe()s an image in an external file.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe(self):
        resource_document_data = super().describe()
        resource_document_data[self.name].update(
            dict(
                external="FILESTORE:",
                dtype="array",
            )
        )
        return resource_document_data


class Timepix4Detector(Device):
    """
    Ophyd Device for Timepix4 detector via HTTP server
    """

    # Signals
    temperature = Cpt(Signal, value=0.0, kind="normal")
    acquire_time = Cpt(Signal, value=1.0, kind="config")
    acquire_period = Cpt(Signal, value=1.0, kind="config")
    num_images = Cpt(Signal, value=1, kind="config")
    root_dir = Cpt(
        Signal,
        value="/nsls2/data/chx/proposals/2025-3/pass-316251/assets/timepix4-1/",
        kind="config",
    )
    file_prefix = Cpt(Signal, value="test", kind="config")
    write_directory = Cpt(Signal, value="/mnt/svm0_vol2/timepix4/", kind="config")
    top_file = Cpt(Signal, value="", kind="hinted")
    bot_file = Cpt(Signal, value="", kind="hinted")
    file_sizes = Cpt(Signal, value=-1, kind="hinted")
    top_data = Cpt(ExternalFileReference, kind="normal")
    bot_data = Cpt(ExternalFileReference, kind="normal")

    def __init__(self, *args, server_host="localhost", server_port=8000, **kwargs):
        super().__init__(*args, **kwargs)
        self.server_host = server_host
        self.server_port = server_port
        self.base_url = "http://{}:{}".format(server_host, server_port)
        self._acquiring = False
        self._asset_docs_cache = deque()

    def _log(self, message):
        """Helper function to print timestamped messages"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[
            :-3
        ]  # Include milliseconds
        print(f"[{timestamp}] {message}")

    def _post_request(self, endpoint, timeout=30, data=None):
        """Send POST request to server using new proxy endpoints"""
        url = self.base_url + endpoint

        try:
            response = requests.post(url, json=data, timeout=timeout)
            response.raise_for_status()
            return response.json()  # Return JSON instead of text
        except requests.RequestException as e:
            raise RuntimeError(
                "HTTP request failed for endpoint {}: {}".format(endpoint, e)
            )

    # TODO - add params to get request
    def _get_request(self, endpoint=""):
        """Send GET request to server"""
        url = self.base_url + endpoint
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()  # Return JSON instead of text
        except requests.RequestException as e:
            raise RuntimeError("HTTP GET request failed: {}".format(e))

    def read_temperature(self):
        """Read detector temperature using proxy endpoint"""
        try:
            result = self._get_request("/detector/readtemp")
            # Extract temperature from proxy response
            if result.get("success") and "data" in result:
                temp = result["data"].get("temperature")
                if temp is not None:
                    self.temperature.put(temp)
                    return temp
            self._log(
                "Failed to read temperature from proxy response: {}".format(result)
            )
            return None
        except Exception as e:
            self._log("Failed to read temperature: {}".format(e))
            return None

    def reset(self, timeout=30):
        """Reset the detector"""
        result = self._post_request("/detector/reset", timeout)
        self._log("Reset result: {}".format(result))
        return result

    def initialize(
        self,
        reset=True,
        num_inits=1,
        configure_dacs=True,
        load_pixel_config=True,
        timeout=60,
    ):
        """Initialize the detector"""
        data = {
            "reset": reset,
            "num_inits": num_inits,
            "configure_dacs": configure_dacs,
            "load_pixel_config": load_pixel_config,
        }
        result = self._post_request("/detector/init", timeout, data)
        self._log("Initialize result: {}".format(result))
        return result

    def unstage(self):
        """Unstage the detector (called after acquisition)"""
        super().unstage()
        self._log("Unstaging detector")
        # Read final temperature
        self.read_temperature()
        self._asset_docs_cache.clear()
        return [self]

    # TODO - this is a long complicated method. Can it be broken up?
    def trigger(self):
        """
        Trigger the detector for one acquisition
        Returns a Status object
        """
        status = Status(self)
        save_status = Status(self)

        period = self.acquire_period.get()

        file_prefix = str(uuid.uuid4())
        self.file_prefix.put(file_prefix)

        for side in ["top", "bot"]:
            suffix = side.upper()

            # Prepare resource for data storage
            root_dir = str(Path(self.root_dir.get()) / Path(f'{datetime.now().strftime("%Y/%m/%d/")}'))
            resource_path = str(Path(root_dir) / Path(f"{file_prefix}_{suffix}.bin"))

            getattr(self, f"{side}_file").put(resource_path)

            self._resource_document, self._datum_factory, _ = compose_resource(
                start={"uid": ""},
                spec="TIMEPIX4_BIN",
                root=root_dir,
                resource_path=resource_path,
                resource_kwargs={},
            )
            self._resource_document.pop("run_start")
            self._asset_docs_cache.append(("resource", self._resource_document))

            datum_document = self._datum_factory(datum_kwargs={})
            self._asset_docs_cache.append(("datum", datum_document))

            getattr(self, f"{side}_data").put(datum_document["datum_id"])

        self._log(f"{self._asset_docs_cache = }")

        def acquire():
            try:
                self._acquiring = True
                self._log("Acquiring for {} seconds...".format(period))

                # Start data acquisition in separate thread (non-blocking)
                period_int = int(
                    round(period + 1.5)
                )  # Add buffer time and round to integer

                def data_acquire():
                    try:
                        self._log("Starting data saving (tempus-receiver)...")
                        acquisition_result = requests.post(
                            self.base_url + "/acquire",
                            json={
                                "file_path": str(
                                    self.write_directory.get() / Path(file_prefix)
                                ),
                                "period": period_int,  # Send integer for binary compatibility
                            },
                        )
                        acquisition_result.raise_for_status()
                        result_data = acquisition_result.json()
                        self._log(
                            "Data saving started successfully: {}".format(result_data)
                        )
                        self._log(
                            "Process ID: {}, File path: {}".format(
                                result_data.get("process_id", "unknown"),
                                result_data.get("file_path", "unknown"),
                            )
                        )

                        # Wait for the actual acquisition period to complete
                        self._log(
                            "Data saving running for {} seconds...".format(period_int)
                        )
                        time.sleep(period_int)
                        self._log(f"{result_data = }")
                        data_status = requests.get(
                            self.base_url + "/data/status",
                            params={
                                "process_id": result_data["process_id"],
                                "file_path": result_data["file_path"],
                            },
                        )
                        self._log(f"{data_status = }\n{data_status.json()}")
                        sizes = sum(data_status.json()["file_sizes"].values())
                        print(f"{sizes = }")
                        self.file_sizes.put(sizes)

                        save_status.set_finished()
                        self._log("Data saving completed successfully")

                    except Exception as e:
                        self._log("Data saving error: {}".format(e))

                data_thread = threading.Thread(target=data_acquire, daemon=True)
                data_thread.start()

                # Start detector acquisition in a separate thread (non-blocking)
                def detector_acquire():
                    try:
                        self._log("Starting detector acquisition...")
                        result = self._post_request(
                            "/detector/acquire", data={"period": period}, timeout=120
                        )
                        self._log(
                            "Detector acquisition started successfully: {}".format(
                                result
                            )
                        )
                        self._log("Detector acquisition completed")
                    except Exception as e:
                        self._log("Detector acquisition error: {}".format(e))

                detector_thread = threading.Thread(target=detector_acquire, daemon=True)
                detector_thread.start()

                # Wait for the acquisition period to complete
                self._log(
                    "Waiting for acquisition period to complete ({:.1f} seconds)...".format(
                        period
                    )
                )
                time.sleep(period)
                self._log("Acquisition period completed")

                # Read temperature after acquisition
                # self.read_temperature()

                self._log("Trigger operation completed successfully")
                self._acquiring = False
                status.set_finished()
            except Exception as e:
                self._acquiring = False
                status.set_exception(e)

        # Run acquisition in a separate thread to avoid blocking
        thread = threading.Thread(target=acquire, daemon=True)
        thread.start()

        return status & save_status

    def reset_link(self, link, timeout=30):
        if link not in [0, 1]:
            raise ValueError("Link must be 0 (top) or 1 (bottom)")
        endpoint = f"/detector/reset_link/{link}"
        self._post_request(endpoint, timeout)

    def get_network_status(self):
        return self._get_request("/network/status")

    def collect_asset_docs(self):
        print("Calling collect_asset_docs...")
        items = list(self._asset_docs_cache)
        self._asset_docs_cache.clear()
        for item in items:
            yield item


if __name__ == "__main__":
    # Example usage
    det = Timepix4Detector(
        "xf11id1-tpx4:",
        name="timepix4",
        server_host="xf11id1-det6.nsls2.bnl.local",  # Point to receiver server
        server_port=8000,
    )
    det.temperature.kind = "hinted"

    # Test basic functionality
    print("Testing Timepix4 Detector Device with Proxy")
    print("-" * 50)

    # Read temperature (via proxy)
    print("\nReading temperature via proxy...")
    temp = det.read_temperature()
    print("Temperature: {} C".format(temp))

    # Test with local receiver server
    det_local = Timepix4Detector(
        "tpx4:", name="timepix4", server_host="127.0.0.1", server_port=8000
    )

    print("\nDevice setup complete. Use with Bluesky:")
    print("  RE(count([det], num=5))")
    print("  RE(scan([det], motor, -1, 1, 10))")
    print("\nNote: Make sure http-server-receiver is running and configured")
    print("      to proxy to the collection server.")
