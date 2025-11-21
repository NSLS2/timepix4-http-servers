#!/usr/bin/env python3
"""
Simple HTTP server that:
- Responds to GET requests with current time
- Responds to POST requests with a greeting using the provided username

Example commands on the client side to access the server on http://xf11id1-timepix4-1.nsls2.bnl.local:8000 (IP=10.66.82.30):

- Read the temperature (GET request):

```
$ curl http://xf11id1-timepix4-1.nsls2.bnl.local:8000/readtemp
29.140965711805507
```

- Reset the detector:

```
$ curl -X POST -H "Content-Type: application/json" -d '{"method":"reset","kwargs":{}}' http://xf11id1-timepix4-1.nsls2.bnl.local:8000
Method called: 'reset'  kwargs: {}
Response: Detector was reset
```

- Initialize the detector:

```
$ curl -X POST -H "Content-Type: application/json" -d '{"method":"init","kwargs":{}}' http://xf11id1-timepix4-1.nsls2.bnl.local:8000
Method called: 'init'  kwargs: {}
Response: Successfully initialized and armed the detector

- Arm the detector:

```
$ curl -X POST -H "Content-Type: application/json" -d '{"method":"arm","kwargs":{}}' http://xf11id1-timepix4-1.nsls2.bnl.local:8000
Method called: 'arm'  kwargs: {}
Response: Successfully armed the detector
```

- Acquire the data for 5 seconds:

```
$ curl -X POST -H "Content-Type: application/json" -d '{"method":"acquire","kwargs":{"period":5}}' http://xf11id1-timepix4-1.nsls2.bnl.local:8000
Method called: 'acquire'  kwargs: {'period': 5}
Response: Acquisition done for 5 seconds
```
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import json
import time as time

from start_tempus import TempusController


class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        """Handle GET request - return current time or specific method results"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Check if this is a specific method request
        if self.path == "/readtemp":
            # Return only the temperature value
            temp_value = self._readtemp()
            response = str(temp_value)
        else:
            # Default behavior - return current time and temperature
            response = "Current time   : {}\nDet temperature: {}\n".format(
                current_time, self._readtemp()
            )

        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(response.encode())

    def do_POST(self):
        """Handle POST request - accept a method and its parameters and return confirmation"""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        allowed_methods = {
            "init": self._init,
            "arm": self._arm,
            "acquire": self._acquire,
            "reset": self._reset,
            "reset_link": self._reset_link,
        }

        try:
            # Try to parse JSON data
            data = json.loads(post_data.decode())
            method = data.get("method", "readtemp")
            kwargs = data.get("kwargs", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON, treat as plain text
            method = post_data.decode().strip() or "readtemp"
            kwargs = {}

        if method not in allowed_methods.keys():
            response = "Method {} is not allowed. Allowed methods:\n{}\n".format(
                method, list(allowed_methods.keys())
            )
            self.send_response(400)
        else:
            resp = allowed_methods[method](**kwargs)
            response = "Method called: '{}'  kwargs: {}\nResponse: {}\n".format(
                method, kwargs, resp
            )

            self.send_response(200)

        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(response.encode())

    def _readtemp(self):
        return self.server.det.readtemp()

    def _init(
        self, num_inits=1, configure_dacs=True, load_pixel_config=True
    ):
        init_detector(
            self.server.det,
            num_inits=num_inits,
            configure_dacs=configure_dacs,
            load_pixel_config=load_pixel_config,
        )
        return "Successfully initialized and armed the detector"

    def _arm(self):
        self.server.det.arm_detector()  # should be sending 9.88 KiB/sec
        return "Successfully armed the detector"

    def _acquire(self, period):
        self.server.det.start_acquisition(shutter_time=period)
        return "Acquisition done for {} seconds".format(period)

    def _reset(self):
        self.server.det.reset()
        return "Detector was reset"

    def _reset_link(self, link_id):
        self.server.det.reset_link(link_id, 0, 1)
        return "Detector link {} was reset".format(link_id)


class Tpx4HTTPServer(HTTPServer):
    def __init__(self, *args, det=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.det = det


def run_server(det, host="10.66.82.30", port=8000):
    """Start the HTTP server"""
    server_address = (host, port)
    httpd = Tpx4HTTPServer(server_address, SimpleHTTPRequestHandler, det=det)
    print("Starting server on http://{}:{}/".format(host, port))
    print("Press Ctrl+C to stop the server")
    httpd.serve_forever()


def init_detector(
    det=None,
    num_inits=1,
    configure_dacs=True,
    load_pixel_config=True,
    arm_det=True,
):
    print("We are now running the init_detector() function.")
    if det is None:
        print("Initializing TempusController")
        det = TempusController()
        time.sleep(1)
    else:
        print("The det object is already instantiated, skipping definition.")
    print("Running initialise() method.")
    det.initialise()
    time.sleep(1)
    print("initialise() method complete!")
    # TODO - should we re-enable this?
    # if reset:
    #    det.reset()
    #    time.sleep(1)
    if configure_dacs:
        print("Running configure_dacs() method from file: ")
        det.configure_dacs()
        time.sleep(1)
        print("Configure_dacs() complete.")
    # Retry initialise and setup_links until both links are ready.
    for i in range(num_inits):
        print("Setting up links.")
        det.setup_links()
        print("Setting up of links complete!")
        time.sleep(1)
    # TODO: parse the output to get the status of links
    if load_pixel_config:
        print("Loading pixel config.")
        det.load_pixel_config()
        print("Pixels configured!")
        time.sleep(1)
    if arm_det:
        print("Arming detector.")
        det.arm_detector()
        print("Detector armed!")
        time.sleep(1)
    return det


if __name__ == "__main__":
    det = init_detector()
    run_server(det)
