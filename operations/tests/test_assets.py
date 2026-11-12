import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from operations.assets import scan_folder,resolve_folder,read_document


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.project=self.root/'goods';self.project.mkdir()
        self.env=patch.dict(os.environ,{'OPERATIONS_ASSET_MAPPINGS':json.dumps({'/host-mac':str(self.root)})})
        self.env.start()
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def test_skip_secrets_and_dependency_dirs_and_boundaries(self):
        (self.project/'README.md').write_text('交付源码和教程\napi_key: sk-123456789012345678901234\n支持Python')
        (self.project/'.env').write_text('password=secret')
        (self.project/'node_modules').mkdir();(self.project/'node_modules'/'bad.txt').write_text('ignore')
        (self.project/'escape.txt').symlink_to('/etc/passwd')
        Image.new('RGB',(20,20),'white').save(self.project/'cover.png')
        result=scan_folder('/host-mac/goods','test',self.root/'static')
        self.assertEqual(len(result['files']),2)
        texts=''.join(d['text'] for d in result['documents'])
        self.assertNotIn('sk-1234',texts)
        self.assertNotIn('root:',texts)
        self.assertEqual(len(result['images']),1)
        with self.assertRaises((ValueError,FileNotFoundError)):resolve_folder('/etc')
        with self.assertRaises(ValueError):resolve_folder('/host-mac')
    def test_docx_text_and_archive_inventory(self):
        p=self.project/'guide.docx'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('word/document.xml','<doc xmlns:w="x"><w:t>教程说明</w:t></doc>')
        self.assertIn('教程说明',read_document(p))
        archive=self.project/'source.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('README.md','source');z.writestr('.env','secret');z.writestr('node_modules/a','secret')
        text=read_document(archive)
        self.assertIn('README.md',text);self.assertNotIn('.env',text);self.assertNotIn('node_modules',text)

    def test_selected_video_is_copied_to_existing_material_media(self):
        payload=b'fixture-mp4-inventory'
        (self.project/'demo.mp4').write_bytes(payload)
        result=scan_folder('/host-mac/goods','video-test',self.root/'static')
        self.assertEqual(len(result['videos']),1)
        self.assertEqual(result['documents'],[])
        target=self.root/'static'/result['videos'][0]['url'].removeprefix('/static/')
        self.assertEqual(target.read_bytes(),payload)

    def test_large_video_uses_original_100mb_cap_without_reading_source_body(self):
        path=self.project/'demo.mp4'
        with path.open('wb') as file:file.truncate(21*1024*1024)
        result=scan_folder('/host-mac/goods','large-video',self.root/'static')
        self.assertEqual(len(result['videos']),1);self.assertEqual(result['videos'][0]['size'],21*1024*1024)
        self.assertEqual(result['files'][0]['status'],'video_selected');self.assertEqual(result['documents'],[])
        with path.open('wb') as file:file.truncate(101*1024*1024)
        result=scan_folder('/host-mac/goods','oversize-video',self.root/'static')
        self.assertEqual(result['videos'],[]);self.assertIn('100MB',result['warnings'][0])
