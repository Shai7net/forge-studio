"""Seekable media delivery stays bounded even for open-ended range requests."""
import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forgestudio import api
from fastapi.responses import FileResponse, StreamingResponse


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "movie.mp4")
        self.data = bytes(range(256)) * 4097
        with open(self.path, "wb") as target:
            target.write(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def response(self, value):
        return api._send(self.path, SimpleNamespace(headers={"range": value}))

    def body(self, response):
        async def read():
            return [chunk async for chunk in response.body_iterator]
        return asyncio.run(read())

    def test_open_ended_stream_has_bounded_reads_and_exact_bytes(self):
        response = self.response("bytes=123-")
        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(int(response.headers["content-length"]), len(self.data) - 123)
        chunks = self.body(response)
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(map(len, chunks)), 256 * 1024)
        self.assertEqual(b"".join(chunks), self.data[123:])

    def test_suffix_and_clamped_end(self):
        for value, start, end in [("bytes=-17", len(self.data) - 17, len(self.data) - 1),
                                  ("bytes=9-23", 9, 23), ("bytes=100-99999999", 100, len(self.data) - 1)]:
            response = self.response(value)
            self.assertEqual(response.headers["content-range"], f"bytes {start}-{end}/{len(self.data)}")
            self.assertEqual(b"".join(self.body(response)), self.data[start:end + 1])

    def test_unsatisfiable_and_invalid_ranges(self):
        for value in ["bytes=99999999-", "bytes=4-2", "bytes=-0"]:
            response = self.response(value)
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.headers["content-range"], f"bytes */{len(self.data)}")
        for value in ["", "bytes=-", "bytes=1-2,4-5", "bytes=1-2junk"]:
            self.assertIsInstance(self.response(value), FileResponse)

    def test_abandoned_iterator_closes_file(self):
        source = open(self.path, "rb")
        with patch("builtins.open", return_value=source):
            iterator = api._file_chunks(self.path, 0, len(self.data) - 1)
            next(iterator)
            self.assertFalse(source.closed)
            iterator.close()
            self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
