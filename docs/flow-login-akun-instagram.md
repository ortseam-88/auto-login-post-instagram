# Login akun Instagram

Panduan umum untuk paket kosong. Tidak ada akun, password, cookie, atau profil
login yang disertakan.

## Melalui UI

1. Jalankan `start-ui.bat`.
2. Buka **Akun**, isi identifier dan password milik Anda. Ikon mata membantu
   memeriksa salah ketik. Setelah disimpan, kolom password dikosongkan.
3. Buka **Jalankan**, pilih **Login saja**, lalu centang akun yang diperlukan.
4. Tekan **Start login**. Chrome memakai profil lokal terpisah untuk tiap akun.
5. Selesaikan 2FA/checkpoint/CAPTCHA secara manual bila diminta. Jangan kirim
   kode, password, cookie, atau token ke log maupun chat.
6. Periksa hasil sebelum memulai posting.

Login dianggap berhasil hanya bila session, navigasi terautentikasi,
identitas akun yang diharapkan, dan tidak adanya checkpoint semuanya sesuai.
Satu kegagalan password tidak diikuti percobaan berulang otomatis.
Jika login sudah terverifikasi tetapi penutupan browser gagal, hasil login tetap
dicatat berhasil dengan peringatan cleanup. Run tetap dianggap bermasalah; periksa
log dan pastikan browser tertutup sebelum melanjutkan.

## Penyimpanan

- Kredensial: `input/akun.txt`, format `identifier|password` satu baris per akun.
- Profil: `sessions/<hash-identifier>/`; dashboard memakai `sessions/dashboard/`.
- Path menyesuaikan lokasi folder program secara otomatis.
- Password tidak ditampilkan kembali pada tabel akun atau hasil.
- Profil tidak boleh dipakai dua proses sekaligus. File kunci kecil harus
  tetap stabil; jangan dihapus saat program berjalan.

Menghapus akun melalui UI menghapus baris kredensial serta profil browsernya,
bukan konten atau ledger posting. Menambahkan akun yang sama lagi memerlukan
login baru, tetapi bukan izin mengulang publikasi lama tanpa pemeriksaan.
Jika pembersihan profil gagal, baris akun belum dihapus. Tutup Chrome akun itu
dan ulangi **Hapus akun**. Jangan menghapus file kunci secara manual; profil
yang terhapus sebagian mungkin memerlukan login ulang.

Saat pindah perangkat/user Windows, cookie dapat tidak bisa dipakai karena
enkripsi dan pemeriksaan perangkat Instagram. Login ulang/verifikasi manual
mungkin diperlukan; tidak perlu mengedit path kode.

## Troubleshooting

- **Password salah:** perbaiki input, jangan melakukan percobaan berulang.
- **Perlu verifikasi:** gunakan browser terlihat, selesaikan langkah resmi.
- **Profil sedang dipakai:** hentikan run/proses terkait terlebih dahulu.
- **Identitas berbeda:** jangan lanjut posting; periksa akun/profil yang dipilih.
- **Runtime hilang:** ekstrak paket lengkap; tidak ada fallback Python/Chrome
  sistem atau pengunduhan otomatis.

Lihat `../README.md` untuk pengaturan worker, portabilitas, dan keamanan data.
