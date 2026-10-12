import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
from loader import extract_stls, latest_zip, read_mesh, recent_stls

ASCII = b'''solid test
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 10 0 0
vertex 0 20 0
endloop
endfacet
endsolid test
'''


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_binary_and_ascii_previews(self):
        path = self.folder / 'triangle.stl'
        for content in (ASCII, b'solid binary'.ljust(80, b'\0') + struct.pack('<I', 1) +
                        struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 10, 0, 0, 0, 20, 0, 0)):
            path.write_bytes(content)
            mesh, dimensions = read_mesh(path)
            self.assertEqual(dimensions, (10, 20, 0))
            self.assertEqual(len(mesh), 1)

    def test_invalid_mesh(self):
        path = self.folder / 'bad.stl'
        path.write_bytes(ASCII.replace(b'vertex 10', b'vertex nan'))
        with self.assertRaises(ValueError):
            read_mesh(path)

    def test_download_window_and_newest_zip(self):
        for name, timestamp in [('fresh.STL', 9900), ('old.stl', 5000), ('new.zip', 9900), ('old.zip', 4000)]:
            path = self.folder / name
            path.touch()
            os.utime(path, (timestamp, timestamp))
        self.assertEqual([p.name for p in recent_stls(self.folder, 1, now=10000)], ['fresh.STL'])
        self.assertEqual(len(recent_stls(self.folder, 3, now=10000)), 2)
        self.assertEqual(latest_zip(self.folder).name, 'new.zip')

    def test_nested_zip_extracts_only_stls(self):
        archive = self.folder / 'models.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('nested/model.STL', ASCII)
            z.writestr('readme.txt', 'ignored')
        paths = extract_stls(archive, self.folder / 'cache')
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_bytes(), ASCII)
        self.assertEqual(extract_stls(archive, self.folder / 'cache'), paths)

    def test_zip_traversal_rejected_before_extract(self):
        archive = self.folder / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('good.stl', ASCII)
            z.writestr('../escape.stl', ASCII)
        with self.assertRaises(ValueError):
            extract_stls(archive, self.folder / 'cache')
        self.assertFalse((self.folder / 'escape.stl').exists())


if __name__ == '__main__':
    unittest.main()
