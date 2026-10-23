# SlideFlow — Chrome extension

Extension gửi video hoặc playlist YouTube đang mở vào SlideFlow qua Chrome Native
Messaging. Dữ liệu chỉ đi từ Chrome đến tiến trình cục bộ trên máy; extension không
đọc cookie, phụ đề hay nội dung trang.

## Cài đặt

1. Đặt `SlideFlow.app` ở vị trí sử dụng lâu dài, thông thường là `/Applications`.
   Nếu di chuyển ứng dụng sau này, chạy lại bước 2.
2. Trong DMG, nhấp đúp **Cài tiện ích Chrome.command**. Khi chạy từ mã nguồn, dùng
   `install_host.command` và có thể truyền đường dẫn ứng dụng:

   ```bash
   ./chrome_extension/install_host.command "/Applications/SlideFlow.app"
   ```

   Trình cài sẽ xác minh bundle, gỡ metadata cách ly của đúng `SlideFlow.app`, rồi
   sao chép cầu nối sang `~/Library/Application Support/SlideFlow/ChromeBridge`.
   Cầu nối là executable macOS thuần được biên dịch từ C và không chứa Python.
   Chrome chỉ chạy bản cầu nối độc lập này, không chạy trực tiếp executable hoặc
   `Python.framework` nằm trong ứng dụng.

   Trình cài cũng chép các file extension vào
   `~/Library/Application Support/SlideFlow/ChromeExtension` để chúng không phụ
   thuộc vào DMG hoặc thư mục mã nguồn.
3. Mở `chrome://extensions`, bật **Developer mode**, chọn **Load unpacked**, rồi
   chọn thư mục `ChromeExtension` mà trình cài vừa mở trong Finder.
4. Ghim extension **SlideFlow — Gửi video** lên thanh công cụ.

## Sử dụng

- Trên trang video YouTube, bấm biểu tượng extension để thêm video ngay vào cuối
  hàng chờ SlideFlow.
- Trên trang playlist, bấm biểu tượng để gửi playlist sang SlideFlow và mở danh
  sách chọn video trong ứng dụng.
- Dấu `✓` trên biểu tượng nghĩa là SlideFlow đã nhận; dấu `!` nghĩa là cầu nối chưa
  được cài hoặc ứng dụng cần được mở lại.
- Nếu trước đây đã cài phiên bản cầu nối nằm trong `SlideFlow.app`, chỉ cần chạy lại
  `install_host.command`; không cần tải lại hay khởi động lại Chrome.

Extension ID cố định: `lgebdjdlladiamggnnnebcjccadfbcfa`.
