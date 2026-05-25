// AUTO-GENERATED — Không chỉnh sửa tay.
// Source  : gemini-code-1778963506444.json
// Script  : process_quiz.py
// Format  : NetQuiz v2 DATA
// 'use strict';

let DATA = {
  "weeks": {
    "7": [
      {
        "id": 1,
        "week": 7,
        "question": "Trong các phát biểu sau phát biểu nào KHÔNG chính xác",
        "explanation": "Phát biểu A sai vì kiến trúc Internet (TCP/IP) linh hoạt, có thể được mô tả bằng 4 hoặc 5 tầng tùy tài liệu, không dùng từ 'bắt buộc' cứng nhắc như vậy.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Chồng giao thức của mạng Internet bắt buộc phải có 5 tầng: 1 tầng ứng dụng (application), 1 tầng giao vận (transport), 1 tầng mạng (network), 1 tầng liên kết dữ liệu (data link), và 1 tầng vật lý (physic)"
          },
          {
            "key": "B",
            "text": "Mạng Internet bao gồm các mạng con kết nối với nhau, mỗi mạng có thể sử dụng các công nghệ hoàn toàn khác nhau ở tầng vật lý và tầng liên kết dữ liệu"
          },
          {
            "key": "C",
            "text": "Chồng giao thức của mạng Internet có thể có tới 7 tầng."
          },
          {
            "key": "D",
            "text": "Mạng Internet bao gồm các mạng con kết nối với nhau, mỗi mạng có thể sử dụng các thuật toán tìm đường khác nhau."
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 2,
        "week": 7,
        "question": "Máy chủ Web proxy dùng cho các máy tính của một trường học cần đặt ở đâu để giảm tải cho đường truyền Internet của trường học đó?",
        "explanation": "Proxy đặt tại mạng nội bộ giúp lưu trữ cache, giảm lưu lượng đi ra Internet.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Ở vị trí bất kỳ trên mạng Internet"
          },
          {
            "key": "B",
            "text": "Trong mạng backbone của nhà cung cấp dịch vụ Internet (ISP) của trường học"
          },
          {
            "key": "C",
            "text": "Trong mạng nội bộ của trường học"
          },
          {
            "key": "D",
            "text": "Gần máy chủ Web mà các máy tính của trường học đó truy cập nhiều nhất"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 3,
        "week": 7,
        "question": "Trong kiến trúc giao thức TCP/IP, tầng IP cung cấp dịch vụ gì cho tầng giao vận?",
        "explanation": "Nhiệm vụ của tầng IP (Network) là định tuyến gói tin dựa trên địa chỉ IP.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Truyền dữ liệu giữa các máy tính với độ trễ không đổi"
          },
          {
            "key": "B",
            "text": "Đảm bảo các gói tin được truyền đến đúng tiến trình của bên nhận"
          },
          {
            "key": "C",
            "text": "Định tuyến gói tin đến đúng địa chỉ IP của bên nhận"
          },
          {
            "key": "D",
            "text": "Đảm bảo truyền tin tin cậy giữa các máy tính có liên kết vật lý trực tiếp"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 4,
        "week": 7,
        "question": "Giao thức nào dùng để gửi email",
        "explanation": "SMTP (Simple Mail Transfer Protocol) dùng để gửi mail.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "a",
            "text": "SSH"
          },
          {
            "key": "b",
            "text": "DNS"
          },
          {
            "key": "c",
            "text": "FTP"
          },
          {
            "key": "d",
            "text": "IMAP"
          },
          {
            "key": "e",
            "text": "HTTP"
          },
          {
            "key": "f",
            "text": "POP3"
          },
          {
            "key": "g",
            "text": "SMTP"
          }
        ],
        "answer": [
          "g"
        ],
        "multi": false
      },
      {
        "id": 5,
        "week": 7,
        "question": "UDP được gọi là giao thức không hướng kết nối (connectionless) vì",
        "explanation": "UDP không duy trì trạng thái kết nối và xử lý mỗi gói tin độc lập.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Cả B và D"
          },
          {
            "key": "B",
            "text": "Tất cả gói tin UDP được đối xử một cách độc lập"
          },
          {
            "key": "C",
            "text": "Tất cả các đáp án đều sai"
          },
          {
            "key": "D",
            "text": "Nó gửi dữ liệu như là một luồng các gói tin liên quan đến nhau"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 6,
        "week": 7,
        "question": "Dựa vào bảng NAT translation table, hãy xác định cột nào là Internet side và Local side",
        "explanation": "Cột 2 là dải 192.168.x.x (Private IP) nên là Local side.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "a",
            "text": "Cột 2 là Internet side, Cột 1 là Local side"
          },
          {
            "key": "b",
            "text": "Cột 1 là Internet side, Cột 2 là Local side"
          }
        ],
        "answer": [
          "b"
        ],
        "multi": false
      },
      {
        "id": 7,
        "week": 7,
        "question": "Ứng dụng mạng có thể được xây dựng theo kiến trúc nào?",
        "explanation": "Gồm P2P, Client-Server và mô hình lai (Hybrid).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Peer-to-peer"
          },
          {
            "key": "B",
            "text": "Client-Server"
          },
          {
            "key": "C",
            "text": "Cả B, A và D"
          },
          {
            "key": "D",
            "text": "Hybrid"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 8,
        "week": 7,
        "question": "Ghép kiểu gói tin tương ứng với các tầng: a. Liên kết dữ liệu, b. Mạng, c. Giao vận, d. Ứng dụng",
        "explanation": "Data link-Frame, Network-Packet, Transport-Segment, App-Message.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "a-ii; b-iii; c-iv; d-i"
          },
          {
            "key": "B",
            "text": "a-i; b-ii; c-iii; d-iv"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 9,
        "week": 7,
        "question": "Mô hình OSI có bao nhiêu tầng?",
        "explanation": "Mô hình OSI chuẩn có 7 tầng.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "5"
          },
          {
            "key": "B",
            "text": "7"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 10,
        "week": 7,
        "question": "Cơ chế retransmission của TCP thực hiện khi nào ở bên gửi?",
        "explanation": "TCP dựa vào timeout để truyền lại gói tin mất.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Khi phát hiện lỗi checksum"
          },
          {
            "key": "B",
            "text": "Khi hết thời gian chờ nhận ACK (timeout)"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 11,
        "week": 7,
        "question": "Các phát biểu về HTTP/FTP (Đúng/Sai)",
        "explanation": "Trong ảnh, các mục e, f, g được đánh dấu Đúng.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "e",
            "text": "Mỗi chu kỳ Request-response của HTTP chỉ chạy trên 1 kết nối TCP"
          },
          {
            "key": "f",
            "text": "HTTP có hai loại bản tin là request và response"
          },
          {
            "key": "g",
            "text": "HTTP có thể truyền file giống FTP"
          }
        ],
        "answer": [
          "e",
          "f",
          "g"
        ],
        "multi": true
      },
      {
        "id": 12,
        "week": 7,
        "question": "Độ dài lớn nhất của một frame chuẩn 802.3 Ethernet là?",
        "explanation": "MTU chuẩn của Ethernet là 1500, cộng thêm header/footer là 1518 bytes.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "1518"
          },
          {
            "key": "B",
            "text": "1230"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 13,
        "week": 7,
        "question": "Địa chỉ IP 127.0.0.1 là:",
        "explanation": "127.0.0.1 là địa chỉ tự phản hồi (localhost).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "địa chỉ loopback"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 14,
        "week": 7,
        "question": "Mô hình (có 7 tầng) dùng để trình bày lý thuyết mạng là?",
        "explanation": "Mô hình lý thuyết 7 tầng là OSI.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "OSI",
            "text": "OSI"
          }
        ],
        "answer": [
          "OSI"
        ],
        "multi": false
      },
      {
        "id": 15,
        "week": 7,
        "question": "Giao thức cấu hình địa chỉ IP cho client?",
        "explanation": "Dynamic Host Configuration Protocol.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "DHCP"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 16,
        "week": 7,
        "question": "Dịch vụ thư điện tử sử dụng giao thức nào ở tầng ứng dụng?",
        "explanation": "Email sử dụng SMTP để gửi và nhận/truyền tải bản tin.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "SMTP"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 17,
        "week": 7,
        "question": "Chức năng của router (Đúng/Sai)",
        "explanation": "Router không ngăn xung đột (của switch/hub) mà dùng để định tuyến.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "b",
            "text": "Lựa chọn tuyến đường"
          },
          {
            "key": "c",
            "text": "Chuyển tiếp gói tin"
          },
          {
            "key": "d",
            "text": "Lọc bỏ các gói tin"
          }
        ],
        "answer": [
          "b",
          "c",
          "d"
        ],
        "multi": true
      },
      {
        "id": 18,
        "week": 7,
        "question": "Lựa chọn chức năng của switch",
        "explanation": "Switch hoạt động dựa trên bảng địa chỉ MAC.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "c",
            "text": "forwarding và lọc frame theo MAC"
          },
          {
            "key": "d",
            "text": "học địa chỉ MAC"
          }
        ],
        "answer": [
          "c",
          "d"
        ],
        "multi": true
      },
      {
        "id": 19,
        "week": 7,
        "question": "Phạm vi của lớp địa chỉ B bắt đầu bằng các bit nhị phân nào?",
        "explanation": "Lớp A (0...), Lớp B (10...), Lớp C (110...).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "c",
            "text": "10..."
          }
        ],
        "answer": [
          "c"
        ],
        "multi": false
      },
      {
        "id": 20,
        "week": 7,
        "question": "Giao thức TCP là một giao thức:",
        "explanation": "TCP hội tụ đủ các tính chất trên.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "Cả 3 đáp án (Hướng kết nối, Bắt tay 3 bước, Tin cậy)"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 21,
        "week": 7,
        "question": "Khung tin từ tầng liên kết dữ liệu máy A gửi đến máy B cùng mạng LAN chứa gì?",
        "explanation": "Tại tầng 2, địa chỉ định danh là MAC nguồn và MAC đích.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "MAC của A, MAC của B"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 22,
        "week": 7,
        "question": "DNS cân bằng tải cho Web server bằng cách nào?",
        "explanation": "DNS xoay vòng (Round Robin) các IP cho cùng 1 tên miền.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Lưu tập địa chỉ IP của các Web server trong bản ghi DNS ứng với tên miền"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 23,
        "week": 7,
        "question": "Trễ phản hồi DNS trong mạng LAN (30ms/chặng) khi có cache?",
        "explanation": "Trong ảnh người dùng điền 120.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "120",
            "text": "120"
          }
        ],
        "answer": [
          "120"
        ],
        "multi": false
      },
      {
        "id": 24,
        "week": 7,
        "question": "Tính Checksum của dãy hexa: 12 00 01 00 A0",
        "explanation": "Tổng bù 1 của các từ 16-bit.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B300",
            "text": "B300"
          }
        ],
        "answer": [
          "B300"
        ],
        "multi": false
      },
      {
        "id": 25,
        "week": 7,
        "question": "Câu nào diễn đạt ĐÚNG về switch?",
        "explanation": "Mỗi cổng switch là 1 miền xung đột (collision), nhưng toàn bộ switch là 1 miền quảng bá (broadcast).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "tạo ra nhiều miền collision và một miền broadcast"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 26,
        "week": 7,
        "question": "Giao thức nào chạy tầng ứng dụng và không dùng TCP?",
        "explanation": "DNS mặc định dùng UDP cổng 53.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "DNS"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 27,
        "week": 7,
        "question": "Câu nào nói là ĐÚNG?",
        "explanation": "Mọi thiết bị IP đều cần subnet mask để xác định mạng con.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Các máy tính đầu cuối luôn có subnet mask"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 28,
        "week": 7,
        "question": "Chức năng của link layer được thực hiện ở đâu trong host?",
        "explanation": "Kết hợp giữa phần cứng (NIC) và phần mềm (Driver/OS).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Network interface card (card mạng) và hệ điều hành"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 29,
        "week": 7,
        "question": "NAT làm việc như thế nào?",
        "explanation": "Network Address Translation.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "Dịch địa chỉ IP công cộng sang địa chỉ IP riêng và ngược lại"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 30,
        "week": 7,
        "question": "Độ lớn trường checksum trong TCP là bao nhiêu bit?",
        "explanation": "Checksum TCP/UDP/IP đều là 16 bit.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "16"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 31,
        "week": 7,
        "question": "Trong TCP, bên gửi biết nghẽn mạng bằng cách nào?",
        "explanation": "Đây là dấu hiệu mất gói, TCP coi đó là do nghẽn.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "Khi hết thời gian timeout hoặc nhận 3 ACK trùng lặp"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 32,
        "week": 7,
        "question": "CIDR cho đích 131.23.151.76. Chọn Interface phù hợp nhất?",
        "explanation": "Sử dụng quy tắc khớp tiền tố dài nhất (longest prefix match).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "1"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 33,
        "week": 7,
        "question": "Đúng/Sai về mạng Internet?",
        "explanation": "Internet truyền tin cậy nhờ TCP ở biên, mạng lõi chỉ chuyển mạch gói.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "a",
            "text": "Nghẽn chủ yếu ở mạng lõi"
          },
          {
            "key": "f",
            "text": "Thiết kế cho các yêu cầu đơn giản nên có thể quy mô rất lớn"
          },
          {
            "key": "g",
            "text": "Có thể đảm bảo truyền dữ liệu tin cậy"
          }
        ],
        "answer": [
          "a",
          "f",
          "g"
        ],
        "multi": true
      },
      {
        "id": 34,
        "week": 7,
        "question": "DHCP cung cấp gì cho client?",
        "explanation": "Ngoài IP còn có Mask, Gateway, DNS.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Địa chỉ IP"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 35,
        "week": 7,
        "question": "Thời gian sống tối đa của bản ghi DNS TTL 31 bit?",
        "explanation": "2^31 giây xấp xỉ 68 năm.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "68 năm"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 36,
        "week": 7,
        "question": "Tìm bit sai (hàng, cột) trong bảng kiểm tra chẵn lẻ 2 chiều?",
        "explanation": "Điểm giao giữa hàng và cột có lỗi bit.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "3,2",
            "text": "(3,2)"
          }
        ],
        "answer": [
          "3,2"
        ],
        "multi": false
      },
      {
        "id": 37,
        "week": 7,
        "question": "Tham số nào quyết định khả năng định tuyến của router?",
        "explanation": "Switching fabric là 'trái tim' của router.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Tốc độ xử lý của switching fabric"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 38,
        "week": 7,
        "question": "HTTP 1.1 với 15 ảnh, số lượng kết nối TCP là?",
        "explanation": "HTTP 1.1 dùng persistent connection mặc định.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "1"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 39,
        "week": 7,
        "question": "Giao thức tìm địa chỉ MAC từ IP?",
        "explanation": "Address Resolution Protocol.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "c",
            "text": "ARP"
          }
        ],
        "answer": [
          "c"
        ],
        "multi": false
      },
      {
        "id": 40,
        "week": 7,
        "question": "Số máy tối đa có file sau 3 giây mô hình client-server?",
        "explanation": "Mỗi giây gửi cho 1 máy, sau 3 giây có 3 máy khách nhận được.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "3",
            "text": "3"
          }
        ],
        "answer": [
          "3"
        ],
        "multi": false
      },
      {
        "id": 41,
        "week": 7,
        "question": "7500 byte qua các MTU 4980, 1500, 2500. Số gói nhận được tại B?",
        "explanation": "Gói sẽ bị phân mảnh theo MTU nhỏ nhất (1500). 7500 / 1500 = 5 gói (hoặc 6 tùy header).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "6"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 42,
        "week": 7,
        "question": "Thuật toán định tuyến nào các router có thông tin đầu vào giống nhau?",
        "explanation": "Mọi router đều có bản đồ mạng (topology) đầy đủ.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Link State"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 43,
        "week": 7,
        "question": "Tầng giao vận gom dữ liệu từ nhiều ứng dụng gọi là gì?",
        "explanation": "Gom dữ liệu là Multiplexing (Đa biến/Ghép kênh).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "Multiplexing",
            "text": "Đa biến (Multiplexing)"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 44,
        "week": 7,
        "question": "Tính trễ truyền 1 gói từ S đến R (3 chặng, 1000 bit, 1000 bps, 1ms delay/link)?",
        "explanation": "Tổng trễ truyền dẫn (3s) + trễ lan truyền (3ms) = 3003ms.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "3003",
            "text": "3003"
          }
        ],
        "answer": [
          "3003"
        ],
        "multi": false
      },
      {
        "id": 45,
        "week": 7,
        "question": "Đúng/Sai về lõi mạng (network core)?",
        "explanation": "Lõi mạng gồm các router chuyển mạch gói.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "a",
            "text": "Vận chuyển gói theo cơ chế chuyển mạch gói"
          },
          {
            "key": "d",
            "text": "Mạng lưới các router kết nối"
          }
        ],
        "answer": [
          "a",
          "d"
        ],
        "multi": true
      },
      {
        "id": 46,
        "week": 7,
        "question": "Router thuộc tầng nào trong mô hình OSI?",
        "explanation": "Router là thiết bị tầng 3.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "tầng network"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 47,
        "week": 7,
        "question": "Máy A (110.2.112.12/20), X (110.2.109.4/20), Y (110.2.105.20/20). Những máy nào cùng subnet?",
        "explanation": "Tính toán phần network address (/20) sẽ thấy X và Y giống nhau.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Máy X và máy Y"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 48,
        "week": 7,
        "question": "Trong CSMA/CD, khi phát hiện xung đột, máy tính sẽ?",
        "explanation": "Cơ chế để tối ưu đường truyền khi có va chạm.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "Phát hiện xung đột và ngừng truyền ngay lập tức"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 49,
        "week": 7,
        "question": "Khi gói rời mạng, Router NAT thay IP nguồn bằng gì?",
        "explanation": "Thay IP private bằng IP public của cổng ngoài router.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Địa chỉ IP của NAT router"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 50,
        "week": 7,
        "question": "Card mạng làm gì khi xảy ra xung đột?",
        "explanation": "Phát tín hiệu jam và dừng truyền.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "D",
            "text": "Dừng truyền ngay lập tức"
          }
        ],
        "answer": [
          "D"
        ],
        "multi": false
      },
      {
        "id": 51,
        "week": 7,
        "question": "Địa chỉ nào định danh tiến trình ứng dụng trên máy đầu cuối?",
        "explanation": "IP định danh máy, Port định danh tiến trình.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "Port address"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 52,
        "week": 7,
        "question": "TCP: Gói 1 (Seq 2000, len 1000), Gói 2 (Seq 3000, len 200). Mất gói 1, nhận được gói 2. ACK gửi về là?",
        "explanation": "TCP dùng ACK tích lũy, báo số byte kế tiếp mong đợi (vẫn mong gói 1).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "C",
            "text": "2000"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 53,
        "week": 7,
        "question": "Đích 203.113.119.1. Dòng nào được chọn trong bảng định tuyến?",
        "explanation": "Khớp dải địa chỉ và subnet mask.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "B",
            "text": "Destination 203.113.64.0/18"
          }
        ],
        "answer": [
          "B"
        ],
        "multi": false
      },
      {
        "id": 54,
        "week": 7,
        "question": "Giao thức UDP thường dùng cho ứng dụng nào?",
        "explanation": "UDP phù hợp cho truyền thông thời gian thực, chấp nhận mất mát nhỏ.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Ứng dụng nói chuyện trực tuyến (voice chat)"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 55,
        "week": 7,
        "question": "Trường nào có trong header của frame Ethernet?",
        "explanation": "Đúng ra là MAC address, nhưng trong lựa chọn hình ảnh là phương án này.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "source and destination network addresses"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      }
    ]
  },
  "week_info": {
    "7": {
      "label": "Tuần 7",
      "desc": "Tầng vận chuyển – UDP & RDT",
      "icon": "📦"
    }
  }
};

// CommonJS export (Node.js / seed.js)
// if (typeof module !== 'undefined') module.exports = DATA;
