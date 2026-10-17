# Postingan, carousel, dan Reel

Panduan ini tidak menyertakan media, akun, permalink, atau riwayat pengguna.
Semua publikasi harus memakai akun dan materi yang Anda berwenang gunakan.

## Alur

1. Impor media pada menu **Media**; file disalin ke folder program.
2. Buat materi pada **Konten**: nama, jenis, urutan media, caption, dan opsi.
3. Simpan. Konten tidak memiliki akun tujuan dan dapat dibuat tanpa akun.
4. Pada **Jalankan**, pilih **Posting sungguhan**, centang konten dan akun.
5. Periksa jumlah pasangan konten–akun, tekan Start, lalu ketik `PUBLISH`.
   Konfirmasi berlaku untuk isi konten yang ditampilkan; perubahan dari tab
   lain sebelum snapshot run membuat Start ditolak, bukan mengganti isinya diam-diam.

## Jenis dan opsi

| Jenis | Media | Opsi khusus |
|---|---|---|
| Foto | Satu gambar | Crop, zoom, filter, penyesuaian, alt text, tag orang |
| Carousel | 2–20 gambar/video berurutan | Crop bersama; opsi foto hanya untuk item gambar |
| Reel | Satu video | Crop, cover gambar atau detik video, trim, audio asli, tag orang |

Caption maksimal 2.200 karakter Unicode dan lima hashtag. Lokasi,
kolaborator, label AI, komentar, dan like/view bergantung pada control yang
tersedia di composer. Program tidak boleh diam-diam melewati opsi yang
requested tetapi tidak dapat diverifikasi.

- Cover gambar harus JPG/JPEG/PNG. Cover gambar dan cover-time tidak boleh dipakai bersamaan.
- Trim membutuhkan start/end yang valid dan tidak melewati durasi sumber.
- Video yang tidak mempunyai audio boleh tetap senyap ketika audio asli
  dipertahankan, hanya setelah composer menegaskan `Video has no audio`.
- Alt text, filter, adjustment, zoom, dan tag koordinat hanya berlaku pada
  gambar. Jangan menerapkannya pada item video carousel.
- Pratinjau lokal menampilkan rasio, bukan hasil pixel akhir filter/zoom.
- Tag/collaborator dapat mengirim notifikasi setelah dipublikasikan. Jangan
  memilih pihak lain hanya untuk uji tanpa izin.

## Perilaku engine

Composer berjalan melalui upload, crop, edit, caption/metadata, kemudian
Share. Input upload memakai file chooser browser. Bingkai crop dibaca pada
ancestor pemotong, bukan dimensi sumber; panel timeline harus stabil sebelum
drag. Pilihan dan slider dibaca kembali; posisi yang tidak dapat dicapai
menyebabkan penolakan, bukan angka palsu.
Pemberitahuan **Video posts are now shared as reels** dapat muncul terlambat
pada carousel campuran. Engine menutup pemberitahuan itu secara eksplisit
sebelum memilih composer/aksi berikutnya dan sebelum membuang draft.

Saran hashtag ditutup dengan memindahkan fokus sebelum pengaturan lain.
Picker kolaborator dikonfirmasi melalui Done. Tag Reel diverifikasi pada
panel Tagged people dengan control Remove tag, bukan sekadar teks saran.
Jika gagal sebelum Share, engine mencoba membuang draft dan menutup browser.

Tidak ada mode draft-only untuk pengguna. `prepare()` adalah fungsi internal,
bukan izin untuk mempublikasikan selama pengujian pengembangan.

## Hasil dan keamanan

Intent `submitting` ditulis ke ledger sebelum Share. Bukti server memerlukan
respons configure HTTP berhasil, `status: ok`, dan kode media yang valid;
konfirmasi composer tetap dapat menjadi bukti terpisah. Klik Share dan
pembacaan respons memakai batas waktu publikasi yang sama. Overlay yang
menghalangi klik atau body yang menggantung tidak menahan run tanpa batas.
Hasil `published` dan keadaan penutupan browser dicatat terpisah. Kegagalan
cleanup diberi peringatan dan tidak mengubah bukti terbit menjadi izin retry.
Bukti server yang sudah selesai diterima tetap dipertahankan jika klik kemudian
melaporkan error navigasi; program tidak menunggu respons baru atau mengklik ulang.

Jangan mengulang hasil yang belum pasti secara otomatis. Periksa profil
Instagram dahulu; jangan menghapus ledger atau mengganti ID untuk memaksa
ulang. Pasangan terkonfirmasi dapat diposting lagi hanya melalui pilihan
**Posting ulang yang sudah berhasil** dan konfirmasi `PUBLISH` baru.

Konfirmasi server membuktikan media dibuat, bukan setiap detail render.
Saat melakukan audit resmi, cocokkan caption, jenis/urutan media, dimensi,
alt text, komentar, like/view, audio dan durasi lewat hasil publik yang
tersedia. Efek pixel/filter dan cover perlu pemeriksaan tersendiri.

## Pengujian aman

Untuk pengembangan, gunakan fixture localhost dan data terisolasi. Real
Instagram hanya digunakan dengan izin eksplisit pemilik akun dan scope
publikasi yang terbatas. Jangan memakai akun, cookie atau materi orang lain.
Uji satu per satu, hentikan bila hasil ambigu, simpan ledger, dan jangan
menghapus posting nyata atau riwayat tanpa persetujuan.

Lihat `../README.md` untuk format CLI, lokasi file, dan portabilitas.
