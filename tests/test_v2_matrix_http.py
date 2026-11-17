"""The displayed matrix and downloads follow the same current drawing records."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from aip.v2.server import Application, Handler


class MatrixHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(self.temp.name)
        self.facility = "facility-local"
        self.app.store.add_sheet({"id":"s1", "facility_id":self.facility, "filename":"drawing.png",
            "title":"Observed drawing", "drawing_number":"D-1", "sheet_number":"1", "revision":"A",
            "width":1000, "height":400, "source_metadata":{},
            "components":[{"id":f"c{i}","tag":tag,"type":kind,"bbox":[i*100,100,i*100+40,140],
                "review_status":"unreviewed"} for i,(tag,kind) in enumerate(
                [("F-9","tank"),("XV-7","valve"),("M-2","pump"),("TK-8","tank")])],
            "connections":[{"id":f"e{i}","source":f"c{i}","target":f"c{i+1}",
                "kind":"direct_undirected", "review_status":"unreviewed"} for i in range(3)]})
        self.server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.imports.executor.shutdown(wait=True,cancel_futures=True)
        self.app.store.db.close()
        self.temp.cleanup()

    def call(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        try:
            headers = {"Content-Type":"application/json"} if body is not None else {}
            connection.request(method,path,json.dumps(body).encode() if body is not None else None,headers)
            response = connection.getresponse()
            content = response.read()
            if response.getheader("Content-Type","").startswith("application/json"):
                content = json.loads(content)
            return response.status,content,dict(response.getheaders())
        finally:
            connection.close()

    def matrix(self):
        status,body,_ = self.call("GET","/api/matrix?"+urlencode({"facility_id":self.facility,"sheet_id":"s1"}))
        self.assertEqual(200,status,body)
        return body

    def test_display_json_csv_and_direct_edge_export_agree(self):
        value = self.matrix()
        self.assertEqual(["F-9","XV-7","M-2","TK-8"],value["order"])
        self.assertEqual([[0,1,0,0],[1,0,1,0],[0,1,0,1],[0,0,1,0]],value["matrix"])
        base = "/api/export?"+urlencode({"facility_id":self.facility,"sheet_id":"s1"})
        status,csv_bytes,headers = self.call("GET",base+"&format=matrix")
        self.assertEqual(200,status)
        self.assertIn("connectivity-matrix.csv",headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        self.assertEqual(value["order"],rows[0][1:])
        self.assertEqual(value["order"],[r[0] for r in rows[1:]])
        self.assertEqual(value["matrix"],[[int(v) for v in r[1:]] for r in rows[1:]])
        _,json_value,_ = self.call("GET",base+"&format=matrix-json")
        self.assertEqual(value,json_value)
        status,edges,_ = self.call("GET",base+"&format=connections-json")
        self.assertEqual(200,status)
        self.assertEqual(value["direct_edges"],edges["connections"])
        _,edge_csv,_ = self.call("GET",base+"&format=connections")
        records = list(csv.DictReader(io.StringIO(edge_csv.decode("utf-8-sig"))))
        self.assertEqual(3,len(records))
        self.assertEqual("F-9",records[0]["source"])

    def test_corrections_change_matrix_without_rerunning_extraction(self):
        before = self.matrix()
        status,_,_ = self.call("POST","/api/action",{"action":"delete_connection","sheet_id":"s1","connection_id":"e1"})
        self.assertEqual(200,status)
        after = self.matrix()
        self.assertEqual(before["order"],after["order"])
        self.assertEqual(0,after["matrix"][1][2])
        self.assertEqual(0,after["matrix"][2][1])
        self.assertEqual(2,len(after["direct_edges"]))
        self.assertEqual(0,after["matrix"][0][2])  # Never export reachability as direct adjacency.

    def test_selected_sheet_is_required_to_belong_to_the_facility(self):
        other = self.app.store.add_facility("Other drawing set")
        status,body,_ = self.call("GET","/api/matrix?"+urlencode({"facility_id":other["id"],"sheet_id":"s1"}))
        self.assertEqual(400,status)
        self.assertIn("error",body)
        status,_,_ = self.call("GET","/api/matrix?sheet_id=s1")
        self.assertEqual(200,status)

    def test_grid_changes_do_not_change_matrix(self):
        before = self.matrix()
        self.app.store.update_sheet("s1",{"grid":{"columns":3,"rows":2}})
        self.assertEqual(before,self.matrix())

    def test_default_demo_passes_only_the_real_input_image_and_index_to_import(self):
        with patch.object(self.app.imports,"submit",return_value={"id":"queued","status":"queued"}) as submit:
            result = self.app.action({"action":"demo"})
        files,facility_id = submit.call_args.args
        self.assertEqual(result["facility_id"],facility_id)
        self.assertEqual(["four_component.png","index.csv"],[name for name,_ in files])
        self.assertTrue(files[0][1].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse(any(name.endswith(("reference.json","edges.csv","adjacency.csv")) for name,_ in files))


if __name__ == "__main__":
    unittest.main()
