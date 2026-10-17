# create by Ortseam
"""Perilaku composer yang terukur di Instagram nyata, direplikasi sebagai fixture lokal: tanpa network."""
import asyncio
import base64
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright
import config
from models import DraftValidationError, Job, MediaItem, PersonTag, UnsupportedOption
from publisher import _crop, _edit_reel, _search_metadata, _settled, _tag_media, discard, prepare


CROP_STAGE = """
<style>#frame{width:720px;height:720px;overflow:hidden;transition:height 1s linear}
#media{width:720px;height:900px}</style>
<div role="dialog"><button>Next</button>
  <button onclick="document.getElementById('menu').hidden=!document.getElementById('menu').hidden"><svg aria-label="Select crop"></svg></button>
  <div id="menu" hidden>
    <button onclick="document.getElementById('frame').style.height='720px'">1:1</button>
    <button onclick="document.getElementById('frame').style.height='405px'">16:9</button>
    <button>Original</button>
  </div>
  <div id="frame"><div id="media" style="background-image:url(data:,x)"></div></div>
</div>"""


VIDEO_CROP_STAGE = """
<style>
  #frame {width:720px;height:720px;overflow:hidden}
  #video-wrapper, video {width:720px;height:900px;overflow:visible}
</style>
<div role="dialog"><button>Next</button>
  <button onclick="document.getElementById('menu').hidden=!document.getElementById('menu').hidden"><svg aria-label="Select crop"></svg></button>
  <div id="menu" hidden>
    <button onclick="document.getElementById('frame').style.height='405px'">16:9</button>
  </div>
  <div id="frame"><div id="video-wrapper"><video id="media" muted></video></div></div>
</div>"""


PREPARE_STAGE = r"""
<style>
  [hidden] {display:none!important}
  #details {position:relative;width:480px;padding:16px}
  #details h1 {margin:0 0 16px;height:36px}
  #caption {min-height:80px;padding:8px;border:1px solid gray}
  #ai-options {padding:16px}
  #suggestions {position:absolute;top:65px;bottom:0;left:0;right:0;z-index:2;background:white}
</style>
<a href="#" onclick="event.preventDefault();document.getElementById('composer').hidden=false">
  <svg aria-label="New post"></svg>New post
</a>
<div role="dialog" id="composer" hidden>
  <section data-stage="upload">
    <h1>Create new post</h1><button onclick="chooseMedia()">Select from computer</button>
  </section>
  <section data-stage="crop" hidden><h1>Crop</h1><button onclick="showStage('edit')">Next</button></section>
  <section data-stage="edit" hidden><h1>Edit</h1><button onclick="showStage('details')">Next</button></section>
  <section data-stage="details" id="details" hidden>
    <h1 tabindex="-1" onclick="dismissHashtags()">Create new post</h1>
    <div id="caption" role="textbox" aria-label="Add a caption..." contenteditable="true" oninput="captionChanged()"></div>
    <div id="ai-options"><span>Add AI label</span><input id="ai" type="checkbox" role="switch" onchange="updateShare()"></div>
    <button id="share" disabled onclick="document.getElementById('publication-state').textContent='Published'">Share</button>
    <div id="suggestions" role="listbox" aria-label="Hashtag suggestions" hidden><div role="option">#fixture</div></div>
    <output id="caption-state"></output>
  </section>
  <div id="uploaded" hidden><output id="uploaded-file"></output><img id="preview" alt="Uploaded media" width="120" height="120"></div>
</div>
<output id="publication-state">Draft only</output>
<script>
  function showStage(name) {
    for (const stage of document.querySelectorAll('[data-stage]')) stage.hidden = stage.dataset.stage !== name;
  }
  function chooseMedia() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg';
    input.hidden = true;
    input.addEventListener('change', () => {
      const file = input.files[0];
      if (!file) return;
      document.getElementById('uploaded-file').textContent = file.name;
      document.getElementById('uploaded').hidden = false;
      const preview = document.getElementById('preview');
      preview.onload = () => showStage('crop');
      preview.src = URL.createObjectURL(file);
      input.remove();
    });
    document.body.append(input);
    input.click();
  }
  function captionChanged() {
    const open = /#[^\s]+$/.test(document.getElementById('caption').innerText);
    document.getElementById('suggestions').hidden = !open;
    document.getElementById('caption-state').textContent = open ? 'Hashtag suggestions open' : 'Caption ready';
    updateShare();
  }
  function dismissHashtags() {
    const suggestions = document.getElementById('suggestions');
    if (!suggestions.hidden) {
      suggestions.hidden = true;
      document.getElementById('caption-state').textContent = 'Hashtag suggestions dismissed';
    }
    document.getElementById('caption').blur();
    updateShare();
  }
  function updateShare() {
    document.getElementById('share').disabled = !document.getElementById('uploaded-file').textContent
      || !document.getElementById('caption').innerText.trim()
      || !document.getElementById('ai').checked || !document.getElementById('suggestions').hidden;
  }
</script>"""


COLLABORATOR_STAGE = """
<style>
  [hidden] {display:none!important}
  #picker {position:fixed;inset:0;z-index:2;background:white;padding:32px}
</style>
<div role="dialog" id="composer">
  <h1>Create new post</h1>
  <input aria-label="Add collaborators" oninput="openPicker(this.value)">
  <div id="chips"></div>
  <button id="share" disabled onclick="document.getElementById('publication-state').textContent='Published'">Share</button>
</div>
<div role="dialog" id="picker" hidden>
  <span>Collaborators</span>
  <button id="candidate" onclick="selectCollaborator()"><span id="candidate-name"></span></button>
  <div id="selection" hidden></div>
  <button id="done" disabled onclick="confirmCollaborator()">Done</button>
</div>
<output id="publication-state">Draft only</output>
<script>
  function openPicker(name) {
    document.getElementById('candidate-name').textContent = name;
    document.getElementById('picker').hidden = false;
  }
  function selectCollaborator() {
    const selection = document.getElementById('selection');
    selection.textContent = document.getElementById('candidate-name').textContent;
    selection.hidden = false;
    document.getElementById('candidate').hidden = true;
    document.getElementById('done').disabled = false;
  }
  function confirmCollaborator() {
    document.getElementById('done').disabled = true;
    const chip = document.createElement('span');
    chip.textContent = document.getElementById('selection').textContent;
    const chips = document.getElementById('chips');
    chips.append(chip);
    // Readback can appear behind the modal before its closing animation finishes.
    setTimeout(() => {
      document.getElementById('picker').hidden = true;
      document.getElementById('share').disabled = false;
    }, 250);
  }
</script>"""


REEL_TAG_STAGE = """
<style>
  [hidden] {display:none!important}
  #tag-portal {position:absolute;left:24px;top:120px;width:420px;padding:16px;background:white;z-index:2}
</style>
<div role="dialog" id="composer">
  <h1 tabindex="-1" onclick="closeTags()">New reel</h1>
  <button aria-label="Tags Tag people" onclick="document.getElementById('tag-portal').hidden=false">Tag people</button>
  <output id="tag-summary">No people tagged</output>
  <button id="share" disabled onclick="document.getElementById('publication-state').textContent='Published'">Share</button>
</div>
<div id="tag-portal" hidden>
  <div id="tag-search">
    <input name="userSearchInput" oninput="showTagCandidate(this.value)">
    <button id="tag-candidate" hidden onclick="selectTag()"><span id="tag-candidate-name"></span></button>
  </div>
  <section id="tagged-people" hidden>
    <span>Tagged people</span>
    <div id="tag-row"><span id="tagged-name"></span><button aria-label="Remove tag">Remove</button></div>
  </section>
</div>
<output id="publication-state">Draft only</output>
<script>
  function showTagCandidate(name) {
    document.getElementById('tag-candidate-name').textContent = name;
    document.getElementById('tag-candidate').hidden = false;
  }
  function selectTag() {
    document.getElementById('tagged-name').textContent = document.getElementById('tag-candidate-name').textContent;
    document.getElementById('tag-search').hidden = true;
    document.getElementById('tagged-people').hidden = false;
  }
  function closeTags() {
    if (document.getElementById('tagged-people').hidden) return;
    setTimeout(() => {
      document.getElementById('tag-portal').hidden = true;
      if (document.querySelector('#tag-row [aria-label="Remove tag"]')) {
        document.getElementById('tag-summary').textContent = 'Tagged: ' + document.getElementById('tagged-name').textContent;
        document.getElementById('share').disabled = false;
      }
    }, 250);
  }
</script>"""


class ComposerStageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=config.chrome_path(), headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={"width": 1000, "height": 1000})
        self.page = await self.context.new_page()

    async def show(self, content):
        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body=content)
        await self.context.route("**/*", serve)
        await self.page.goto(config.INSTAGRAM_LOGIN_URL)

    async def test_crop_accepts_frame_ratio_after_animation_and_closes_menu(self):
        # Media element stays 4:5 (file ratio); only the clipping frame reaches 16:9, after a 1 s animation.
        await self.show(CROP_STAGE)
        await _crop(self.page, MediaItem(Path("fixture.jpg"), crop="16:9"))
        self.assertEqual(await self.page.evaluate("document.getElementById('frame').getBoundingClientRect().height"), 405)
        self.assertTrue(await self.page.evaluate("document.getElementById('menu').hidden"), "menu crop harus ditutup sebelum tahap zoom")

    async def test_crop_rejects_frame_that_never_reaches_ratio(self):
        await self.show(CROP_STAGE.replace("style.height='405px'", "style.height='600px'"))
        with self.assertRaises(DraftValidationError):
            await _crop(self.page, MediaItem(Path("fixture.jpg"), crop="16:9"))

    async def test_discard_falls_back_to_close_button_when_escape_is_swallowed(self):
        await self.show("""<div role="dialog" id="composer">
          <button aria-label="Close" onclick="document.getElementById('confirm').hidden=false">X</button>
          <div id="confirm" hidden role="dialog"><button onclick="document.getElementById('composer').remove();document.getElementById('confirm').remove()">Discard</button></div>
        </div>
        <script>document.addEventListener('keydown', e => { if (e.key === 'Escape') e.stopImmediatePropagation(); }, true);</script>""")
        await discard(self.page)
        self.assertEqual(await self.page.locator("[role=dialog]").count(), 0)

    async def test_late_video_notice_is_dismissed_before_crop_and_discard(self):
        await self.show(CROP_STAGE + '''
        <div role="dialog" id="video-notice" style="position:fixed;inset:0;background:white;z-index:5">
          <h1>Video posts are now shared as reels</h1>
          <button onclick="document.getElementById('video-notice').remove()">OK</button>
        </div>
        <script>
          document.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
              const confirm = document.createElement('div'); confirm.setAttribute('role','dialog');
              confirm.innerHTML = '<button onclick="document.querySelectorAll(\\'[role=dialog]\\').forEach(n=>n.remove())">Discard</button>';
              document.body.append(confirm);
            }
          });
        </script>''')
        await _crop(self.page, MediaItem(Path("fixture.jpg"), crop="1:1"))
        self.assertEqual(await self.page.locator("#video-notice").count(), 0)
        await self.page.evaluate("""() => {
            const notice=document.createElement('div'); notice.id='video-notice'; notice.setAttribute('role','dialog');
            notice.innerHTML='<h1>Video posts are now shared as reels</h1><button onclick="this.parentElement.remove()">OK</button>';
            document.body.append(notice);
        }""")
        await discard(self.page)
        self.assertEqual(await self.page.get_by_role("dialog").count(), 0)

    async def test_keep_original_audio_accepts_confirmed_silent_video(self):
        await self.show('''<div role="dialog"><button>Next</button>
            <span>Video has no audio</span><input role="switch" type="checkbox" disabled>
            </div>''')
        job = Job("silent-reel", "fixture_account", "reel", (MediaItem(Path("fixture.mp4")),),
                  keep_original_audio=True)
        await _edit_reel(self.page, job)
        audio = self.page.get_by_role("switch")
        self.assertFalse(await audio.is_checked(), "Video tanpa audio harus tetap tanpa suara tambahan.")
        self.assertFalse(await audio.is_enabled())

    async def test_disabled_audio_without_silent_video_proof_is_rejected(self):
        await self.show('''<div role="dialog"><button>Next</button>
            <span>Audio is loading</span><input role="switch" type="checkbox" disabled>
            </div>''')
        job = Job("unknown-audio", "fixture_account", "reel", (MediaItem(Path("fixture.mp4")),),
                  keep_original_audio=True)
        with self.assertRaises(UnsupportedOption):
            await _edit_reel(self.page, job)

    async def test_video_crop_uses_clipping_frame_beyond_nonclipping_wrapper(self):
        await self.show(VIDEO_CROP_STAGE)
        await _crop(self.page, MediaItem(Path("fixture.mp4"), crop="16:9"))
        frame = await self.page.locator("#frame").bounding_box()
        wrapper = await self.page.locator("#video-wrapper").bounding_box()
        self.assertAlmostEqual(frame["width"] / frame["height"], 16 / 9, delta=0.001)
        self.assertAlmostEqual(wrapper["width"] / wrapper["height"], 4 / 5, delta=0.001)
        self.assertFalse(await self.page.locator("#menu").is_visible())

    async def test_video_crop_rejects_wrong_frame_even_when_source_matches(self):
        fixture = VIDEO_CROP_STAGE.replace("height:900px", "height:405px").replace(
            "style.height='405px'", "style.height='600px'"
        )
        await self.show(fixture)
        with self.assertRaises(DraftValidationError):
            await _crop(self.page, MediaItem(Path("fixture.mp4"), crop="16:9"))
        frame = await self.page.locator("#frame").bounding_box()
        source = await self.page.locator("#media").bounding_box()
        self.assertAlmostEqual(frame["width"] / frame["height"], 6 / 5, delta=0.001)
        self.assertAlmostEqual(source["width"] / source["height"], 16 / 9, delta=0.001)

    async def test_settled_waits_through_delayed_translation_and_resize(self):
        await self.show('''<div id="timeline" role="slider" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"
            style="position:absolute;left:40px;top:40px;width:240px;height:24px;background:gray"></div>''')
        await self.page.evaluate("""() => {
          const timeline = document.getElementById('timeline');
          window.finishedAt = null;
          window.motions = [
            timeline.animate([{transform:'translate(0,0)'}, {transform:'translate(64px,32px)'}],
              {delay:350,duration:300,fill:'forwards'}),
            timeline.animate([{width:'240px',height:'24px'}, {width:'320px',height:'48px'}],
              {delay:900,duration:550,fill:'forwards'})
          ];
          Promise.all(window.motions.map(animation => animation.finished)).then(() => {
            window.finishedAt = performance.now();
          });
        }""")
        await _settled(self.page.locator("#timeline"), timeout=4.0)
        state = await self.page.evaluate("""() => ({
          states: window.motions.map(animation => animation.playState),
          quietFor: window.finishedAt === null ? 0 : performance.now() - window.finishedAt,
          box: document.getElementById('timeline').getBoundingClientRect().toJSON()
        })""")
        self.assertEqual(state["states"], ["finished", "finished"])
        self.assertGreaterEqual(state["quietFor"], 550, "Timeline harus tetap diam, bukan hanya melewati dua sampel awal.")
        for dimension, expected in {"x": 104, "y": 72, "width": 320, "height": 48}.items():
            self.assertAlmostEqual(state["box"][dimension], expected, delta=0.5)

    async def test_prepare_uses_filechooser_and_dismisses_hashtags_before_ai_label(self):
        self.page.set_default_timeout(2500)
        await self.show(PREPARE_STAGE)
        self.assertEqual(await self.page.locator('input[type="file"]').count(), 0)
        directory = self.enterContext(tempfile.TemporaryDirectory())
        media = Path(directory) / "fixture.png"
        encoded_png = await self.page.evaluate("""() => {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 8;
          canvas.getContext('2d').fillRect(0, 0, 8, 8);
          return canvas.toDataURL('image/png').split(',')[1];
        }""")
        media.write_bytes(base64.b64decode(encoded_png))
        job = Job("local-prepare", "fixture_account", "photo", (MediaItem(media),),
                  caption="Local fixture caption #fixture", ai_label=True)
        await asyncio.wait_for(prepare(self.page, job), timeout=10)
        self.assertEqual(await self.page.locator("#uploaded-file").inner_text(), media.name)
        self.assertEqual(await self.page.locator("#preview").evaluate("image => image.naturalWidth"), 8)
        self.assertEqual(await self.page.get_by_role("textbox", name="Add a caption...", exact=True).inner_text(), job.caption)
        self.assertEqual(await self.page.locator("#caption-state").inner_text(), "Hashtag suggestions dismissed")
        self.assertFalse(await self.page.get_by_role("listbox", name="Hashtag suggestions").is_visible())
        self.assertTrue(await self.page.get_by_role("switch").is_checked())
        self.assertTrue(await self.page.get_by_role("button", name="Share", exact=True).is_enabled())
        self.assertEqual(await self.page.locator("#publication-state").inner_text(), "Draft only")

    async def test_collaborator_requires_done_and_waits_for_picker_to_close(self):
        self.page.set_default_timeout(2500)
        await self.show(COLLABORATOR_STAGE)
        await asyncio.wait_for(_search_metadata(self.page, "Add collaborators", "fixture_collaborator"), timeout=8)
        self.assertFalse(await self.page.locator("#picker").is_visible())
        chip = self.page.locator("#chips").get_by_text("fixture_collaborator", exact=True)
        self.assertTrue(await chip.is_visible())
        self.assertEqual(await chip.count(), 1)
        self.assertTrue(await self.page.get_by_role("button", name="Share", exact=True).is_enabled())
        self.assertEqual(await self.page.locator("#publication-state").inner_text(), "Draft only")

    async def test_collaborator_rejects_closed_picker_without_persisted_chip(self):
        self.page.set_default_timeout(2500)
        await self.show(COLLABORATOR_STAGE.replace("chips.append(chip);", "chip.remove();"))
        with self.assertRaises(DraftValidationError):
            await asyncio.wait_for(_search_metadata(self.page, "Add collaborators", "fixture_collaborator"), timeout=8)
        self.assertFalse(await self.page.locator("#picker").is_visible())
        self.assertEqual(await self.page.locator("#chips").get_by_text("fixture_collaborator", exact=True).count(), 0)
        self.assertEqual(await self.page.locator("#publication-state").inner_text(), "Draft only")

    async def test_reel_tag_accepts_selected_portal_and_closes_it_before_returning(self):
        self.page.set_default_timeout(2500)
        await self.show(REEL_TAG_STAGE)
        job = Job("local-reel-tag", "fixture_account", "reel", (MediaItem(Path("fixture.mp4")),),
                  people_tags=(PersonTag("fixture_tag"),))
        await asyncio.wait_for(_tag_media(self.page, job), timeout=8)
        self.assertFalse(await self.page.locator("#tag-portal").is_visible())
        self.assertEqual(await self.page.locator("#tag-summary").inner_text(), "Tagged: fixture_tag")
        self.assertTrue(await self.page.get_by_role("button", name="Share", exact=True).is_enabled())
        self.assertEqual(await self.page.locator("#publication-state").inner_text(), "Draft only")

    async def test_reel_tag_rejects_recommendation_without_remove_tag_proof(self):
        self.page.set_default_timeout(2500)
        await self.show(REEL_TAG_STAGE.replace('<button aria-label="Remove tag">Remove</button>', '<span>Suggested account</span>'))
        job = Job("local-unconfirmed-reel-tag", "fixture_account", "reel", (MediaItem(Path("fixture.mp4")),),
                  people_tags=(PersonTag("fixture_tag"),))
        with self.assertRaises(DraftValidationError):
            await asyncio.wait_for(_tag_media(self.page, job), timeout=8)
        self.assertTrue(await self.page.locator("#tagged-name").is_visible())
        self.assertEqual(await self.page.locator("#tagged-name").inner_text(), "fixture_tag")
        self.assertTrue(await self.page.locator("#tag-portal").is_visible())
        self.assertEqual(await self.page.locator("#tag-summary").inner_text(), "No people tagged")
        self.assertFalse(await self.page.get_by_role("button", name="Share", exact=True).is_enabled())
        self.assertEqual(await self.page.locator("#publication-state").inner_text(), "Draft only")


if __name__ == "__main__":
    unittest.main()
