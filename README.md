# Instagram Program — Ortseam

Program portable untuk login dan memposting foto, carousel, atau Reel melalui
composer web Instagram. Paket ini disiapkan **tanpa akun, konten, media,
session login, maupun riwayat publikasi**.

## Menjalankan

1. Gunakan **Windows 10/11 x64**.
2. Ekstrak seluruh folder dari ZIP ke lokasi yang dapat ditulis. Jangan
   menjalankan langsung dari tampilan ZIP atau dari folder sistem.
3. Klik dua kali **`start-ui.bat`**. Dashboard localhost dibuka di Chrome
   bawaan paket. Alamat default `http://127.0.0.1:8765/`; port berikutnya
   dipilih jika port tersebut sedang dipakai.
4. Tambahkan akun sendiri, impor media, siapkan konten, lalu jalankan.

Python, Tk/Tcl, Playwright beserta driver, dependensi, Chrome dan font UI
sudah disertakan. Tidak perlu memasang Python/Node/Chrome tambahan. Internet
hanya diperlukan untuk berinteraksi dengan Instagram. Jika komponen runtime
hilang, ekstrak ulang paket lengkap; program tidak mengunduh pengganti otomatis.

Untuk server tanpa membuka dashboard:

```powershell
.\start-ui.bat --no-browser
```

Setelah memperbarui kode, hentikan run aktif dan tutup server lama sebelum
membuka ulang launcher. Refresh halaman saja tidak memuat ulang backend Python.

## Alur penggunaan

### Akun

Tambahkan username/email/nomor HP dan password melalui **Tambah akun**. Ikon
mata menampilkan atau menyembunyikan password saat mengetik; setelah berhasil
tersimpan, kolom dikosongkan dan disembunyikan kembali. Password tersimpan
bukan untuk ditampilkan kembali oleh UI.

**Impor banyak akun** menerima satu `identifier|password` per baris. Baris
kosong atau berawalan `#` diabaikan. Akun duplikat dilewati; satu baris tidak
valid membatalkan impor. Spasi dalam password dipertahankan.
Selama penyimpanan, tombol kirim dinonaktifkan; input untuk akun/batch berikutnya
boleh diketik dan tidak dihapus oleh respons sebelumnya. Jika akun sudah tersimpan
tetapi daftar gagal dimuat ulang, UI menjelaskannya; muat ulang tanpa menambah ulang.

Kredensial tersimpan dalam file lokal `input/akun.txt`; lindungi folder ini.
**Hapus akun** juga menghapus profil browser akun tersebut. Konten dan ledger
publikasi tidak ikut dihapus. Penghapusan ditolak saat run sedang memakai
inventaris/profil. File kunci kecil dapat tetap ada tanpa menyimpan password
atau cookie; jangan menghapus file kunci saat program berjalan.
Jika file profil terkunci atau pembersihan gagal, akun tetap ada agar penghapusan
bisa dicoba lagi setelah Chrome akun itu ditutup. Profil yang sempat terhapus
sebagian mungkin perlu login ulang bila akun digunakan kembali.

### Media

Gunakan **Pilih file dari komputer** atau masukkan path file lewat opsi impor.
Program menyalin media ke `input/media/` agar folder dapat dipindahkan.

- Gambar: JPG/JPEG, PNG, AVIF, HEIC, HEIF.
- Video: MP4 atau MOV; ketersediaan format akhir bergantung pada composer.
- Nama file yang didukung: huruf/angka ASCII, titik, garis bawah, tanda minus,
  dan spasi. Gunakan nama unik bila dua file memiliki nama sama.
- **Hapus media** memindahkan file ke `archives/media-dihapus/`, bukan langsung
  menghapus permanen. Media yang dirujuk konten tersimpan atau perubahan editor
  yang belum disimpan harus dilepas dahulu.

### Konten

Satu kartu berisi satu konten: nama, media, caption, dan pengaturan posting.
**Tidak ada pilihan akun di sini**. Konten boleh disimpan sebelum akun dibuat.

- Foto: satu gambar; Reel: satu video; carousel: 2–20 media berurutan.
- Caption maksimum 2.200 karakter Unicode dan lima hashtag.
- Pilihan foto: crop, zoom, filter, penyesuaian, alt text, serta tag orang.
- Pilihan Reel: crop, cover dari gambar **atau** detik video, trim, audio asli,
  serta tag orang.
- Pilihan umum: lokasi, kolaborator, label AI, komentar, dan like/view.
- Pratinjau lokal menunjukkan rasio saja. Filter/zoom diterapkan pada composer,
  bukan direproduksi secara persis oleh pratinjau lokal.
- Konten yang dimuat tampil terlipat. Konten baru/duplikat langsung terbuka;
  tersedia pencarian, urutan, dan Lipat/Buka semua.

Simpan memvalidasi konten sebelum menulis file. Edit baru yang terjadi saat
penyimpanan berlangsung tetap ditandai belum disimpan.
Respons refresh lama tidak menimpa simpan terbaru. Pemilih media dan konfirmasi
hapus tetap mengacu pada identitas konten meskipun daftar diperbarui.

Katalog `input/jobs.json` menggunakan versi 2 dengan `contents` dan `bindings`.
Identitas internal tidak berubah saat nama konten disunting. `bindings` milik
backend menyimpan identitas percobaan posting, bukan akun tujuan. Jangan
mengubahnya untuk menghindari perlindungan posting ulang.

### Jalankan

1. Pilih **Login saja** atau **Posting sungguhan**.
2. Untuk posting, centang konten yang akan dikirim.
3. Centang akun tujuan; tersedia **Pilih semua akun** dan **Batal pilih**.
4. Periksa ringkasan, misalnya `2 konten × 3 akun = 6 postingan`.
5. Tekan **Start**. Posting meminta ketikan **`PUBLISH`**.

Pilihan awal kosong. Setiap konten terpilih dikirim ke setiap akun terpilih.
Login hanya memakai pilihan akun. Start menyimpan perubahan konten/pengaturan
sebelum menjalankan engine yang sama sebagai proses terpisah.
Isi konten yang dikonfirmasi ikut diperiksa saat snapshot run dibuat. Jika
tab lain mengubahnya, Start ditolak dan isi terbaru harus diperiksa kembali.
Konfirmasi dapat dikirim lewat tombol Posting atau Enter pada kolom `PUBLISH`.

Default paket: **Login saja**, **5 akun paralel**, batas verifikasi manual
**300 detik**, dan browser terlihat. Batas worker 1–10; pekerjaan akun yang
sama tetap berurutan. Dashboard adalah browser tambahan di luar worker.
Jika file pengaturan rusak, UI tetap terbuka dengan peringatan dan Start
diblokir. Perbaiki nilainya lewat **Pengaturan lanjutan** sebelum menjalankan.

2FA/checkpoint/CAPTCHA harus diselesaikan pemilik akun secara manual. Tidak
ada solver, bypass, rotasi IP/VPN, atau percobaan password berulang. Jangan
memakai headless bila akun masih perlu verifikasi manual.

**Stop** mengirim permintaan penghentian. UI menampilkan **Sedang menghentikan**
sampai backend menyelesaikan proses; Start dan Stop dinonaktifkan sementara.
Status **Dihentikan** baru muncul setelah proses benar-benar berakhir.
Tunggu sampai selesai sebelum menutup server, menyalin folder, atau membuat ZIP.
Di Windows, sinyal Stop ditangani sebagai pembatalan task agar cleanup tetap
berjalan; permintaan Stop berulang tidak mengirim ulang sinyal saat penutupan.

### Hasil, riwayat, dan posting ulang

Hasil mencatat akun, konten, status, detail, waktu, dan permalink bila tersedia.
**Siapkan ulang** hanya memilih scope untuk diperiksa; tidak langsung memposting.
Status publikasi dan penutupan browser/penyimpanan hasil diperiksa terpisah.
Posting terkonfirmasi dengan cleanup gagal ditandai perlu perhatian, bukan
dianggap seluruh proses sukses atau diberi percobaan ulang otomatis.

Pasangan konten–akun yang sudah berhasil dilewati secara default. Aktifkan
**Posting ulang yang sudah berhasil** hanya jika memang ingin publikasi baru;
konfirmasi `PUBLISH` tetap wajib. Posting ulang tidak membuat kartu konten
baru dan tidak menghapus catatan lama.

Hasil `submitting`, `unknown_after_submit`, atau `unknown` **tidak boleh diulang
otomatis**. Periksa profil Instagram dahulu. Jangan menghapus ledger atau
mengganti ID agar program memaksa ulang; hal itu dapat menyebabkan duplikasi.

### Sistem

Menu Sistem menampilkan lokasi aktual program, runtime, media, session dan
file data. Path dihitung dari lokasi folder program, bukan dikunci ke drive
atau nama user Windows tertentu.

## Folder dan portabilitas

```text
instagram-program/
  app/                 kode, UI, tes, dan chrome-portable/
  runtime/             Python, Tk/Tcl, dependensi, driver Playwright
  docs/                panduan umum tanpa kredensial
  input/               akun.txt, jobs.json, ui-settings.json, media/
  output/              hasil, ledger, riwayat, file kunci
  logs/                log lokal yang dibuat saat digunakan
  sessions/            profil akun/dashboard yang dibuat saat digunakan
  archives/            media yang dihapus melalui UI
  evidence/            kosong pada paket distribusi
  start-ui.bat
  start_ui.py
  README.md
```

Launcher memakai `%~dp0` dan Python bawaan. Semua lokasi default diturunkan
dari `__file__`; media/cover yang diimpor memakai referensi relatif. Runtime
Python mengabaikan registry, paket user, `PYTHONHOME` dan `PYTHONPATH` melalui
`python313._pth`. Launcher juga mengosongkan override browser/session/Node
untuk prosesnya sendiri. Modul pihak ketiga dan lisensinya tetap disertakan.
Versi/checksum komponen tercatat pada `runtime/bundle-info.json`.
Pembaca dan penulis state memakai kunci singkat `.instagram-state.lock`
agar pembacaan status Windows tidak menghalangi penulisan atomik ledger.
Jangan menghapus file kunci saat program atau engine masih aktif.

Paket kosong dapat di-ZIP dan dipindahkan tanpa mengedit path. Ekstrak lengkap
pada Windows x64, lalu jalankan launcher. Belum ada jaminan untuk Linux,
macOS, Android, atau lingkungan Windows dengan kebijakan yang memblokir executable.
Uji pindah folder pada satu komputer bukan bukti pengujian semua komputer.

**Setelah program dipakai**, folder akan mengandung data pribadi. Untuk
melanjutkan pekerjaan di perangkat lain, bawa data terbaru termasuk ledger
serta media; jangan meresetnya hanya karena berpindah perangkat. Cookie dapat
memerlukan login ulang pada akun/perangkat Windows lain. Dua salinan program
mempunyai ledger terpisah dan **tidak saling tersinkron**.

Jika membagikan paket kosong, jangan sertakan backup pribadi di luar folder
program. Jangan membagikan `akun.txt`, cookie/session, media, atau riwayat
milik pengguna lain. Publikasi yang sudah ada di Instagram tidak dihapus
oleh pembersihan file lokal.

## CLI alternatif

Gunakan Python bawaan dari PowerShell pada folder program:

```powershell
.\runtime\python\python.exe -B -X utf8 app/main.py --mode login
.\runtime\python\python.exe -B -X utf8 app/main.py --mode login --account akun_anda
```

`--account` boleh diulang untuk membatasi beberapa akun. `--workers` 1–10,
default 5. CLI publish memerlukan manifest eksekusi **versi 1**, bukan katalog
UI versi 2. Buat file sendiri, misalnya `input/run-jobs.json`:

```json
{
  "version": 1,
  "jobs": [{
    "job_id": "publikasi-001",
    "account": "akun_anda",
    "kind": "photo",
    "caption": "Caption Anda",
    "media": [{"path": "media/foto-anda.jpg"}]
  }]
}
```

Contoh ini bukan data akun/media yang disertakan; gunakan akun terdaftar dan
file nyata Anda. `--check` tidak membuka browser atau memposting:

```powershell
.\runtime\python\python.exe -B -X utf8 app/main.py --mode publish --jobs input/run-jobs.json --check
```

Hapus `--check` hanya jika memang ingin publikasi nyata. `--job <id>` boleh
diulang untuk membatasi job. Path CLI relatif folder program; path media
relatif folder manifest. ID job stabil dan digest media melindungi replay.

Exit 0: berhasil. Exit 1: ada kegagalan/hasil ambigu. Exit 2: input tidak valid.
Exit 130: dihentikan. Riwayat UI menyimpan maksimal 100 run.

## Pengujian lokal

```powershell
.\runtime\python\python.exe -B -X utf8 -m unittest discover -s app/tests -q
```

Tes memakai fixture/data sementara dan tidak mempublikasikan ke Instagram.
Pengujian publikasi sungguhan harus memiliki izin pemilik akun yang jelas.
UI memakai Material Design 3 dengan Roboto dan Material Symbols yang di-host
lokal. Komentar `create by Ortseam` berlaku pada script proyek; library/runtime
pihak ketiga tetap mengikuti atribusi dan lisensi aslinya.
