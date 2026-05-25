// AUTO-GENERATED — Không chỉnh sửa tay.
// Source  : deepseek_json_20260516_c44c44.json
// Script  : process_quiz.py
// Format  : NetQuiz v2 DATA
// 'use strict';

let DATA = {
  "weeks": {
    "7": [
      {
        "id": 1,
        "week": 7,
        "question": "Khi nhiều máy khách UDP gửi các phân đoạn UDP đến cùng một số cổng đích tại máy chủ nhận, các phân đoạn đó (từ những người gửi khác nhau) sẽ luôn được chuyển hướng đến cùng một socket tại máy chủ nhận.",
        "explanation": "UDP không có kết nối, các phân đoạn từ các máy khách khác nhau đến cùng cổng đích sẽ được chuyển đến cùng một socket.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Đúng"
      },
      {
        "id": 2,
        "week": 7,
        "question": "Khi nhiều máy khách TCP gửi các phân đoạn TCP đến cùng một số cổng đích tại máy chủ nhận, các phân đoạn đó (từ những người gửi khác nhau) sẽ luôn được chuyển hướng đến cùng một socket tại máy chủ nhận.",
        "explanation": "TCP phân biệt các kết nối bằng bộ 4 (địa chỉ IP nguồn, cổng nguồn, địa chỉ IP đích, cổng đích), do đó mỗi kết nối có một socket riêng.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Sai"
      },
      {
        "id": 3,
        "week": 7,
        "question": "Có thể gửi hai phân đoạn UDP từ cùng một socket với cổng nguồn 5723 tại một máy chủ tới hai máy khách khác nhau.",
        "explanation": "UDP socket có thể gửi dữ liệu đến nhiều địa chỉ đích khác nhau.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Đúng"
      },
      {
        "id": 4,
        "week": 7,
        "question": "Hai phân đoạn TCP có cổng nguồn 80 có thể được gửi bởi máy chủ đến các máy khách khác nhau.",
        "explanation": "Máy chủ web (cổng 80) có thể phục vụ nhiều client đồng thời, mỗi client có một socket riêng.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Đúng"
      },
      {
        "id": 5,
        "week": 7,
        "question": "Về phía gửi, người gửi UDP sẽ lấy từng đoạn dữ liệu của lớp ứng dụng được ghi vào UDP socket và gửi nó trong một gói dữ liệu UDP riêng biệt. Và sau đó ở phía nhận, UDP sẽ phân phối payload của phân đoạn vào socket thích hợp, duy trì ranh giới thông báo do ứng dụng xác định.",
        "explanation": "UDP bảo toàn ranh giới thông báo, mỗi lần ghi socket tương ứng một datagram.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Đúng"
      },
      {
        "id": 6,
        "week": 7,
        "question": "Trường nào dưới đây nằm trong tiêu đề phân đoạn UDP?",
        "explanation": "UDP header gồm: Source port (16 bit), Destination port (16 bit), Length (16 bit), Checksum (16 bit).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Source IP address"
          },
          {
            "key": "B",
            "text": "Internet checksum"
          },
          {
            "key": "C",
            "text": "Destination port number"
          },
          {
            "key": "D",
            "text": "Length (of UDP header plus payload)"
          },
          {
            "key": "E",
            "text": "Sequence number"
          },
          {
            "key": "F",
            "text": "Upper layer protocol"
          },
          {
            "key": "G",
            "text": "Data (payload)"
          },
          {
            "key": "H",
            "text": "Source port number"
          }
        ],
        "answer": [
          "B",
          "C",
          "D",
          "H"
        ],
        "multi": true
      },
      {
        "id": 7,
        "week": 7,
        "question": "Why is the UDP header length field needed?",
        "explanation": "Trường length cho biết kích thước toàn bộ segment (header + payload), giúp UDP biết payload kết thúc ở đâu.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Because the payload section can be of variable length, and this lets UDP know where the segment ends."
          },
          {
            "key": "B",
            "text": "Because this field is needed in TCP as well."
          },
          {
            "key": "C",
            "text": "To make the header and even number of bytes and Because this field is needed in TCP as well."
          },
          {
            "key": "D",
            "text": "To make the header and even number of bytes"
          }
        ],
        "answer": [
          "A"
        ],
        "multi": false
      },
      {
        "id": 8,
        "week": 7,
        "question": "Over what set of bytes is the checksum field in the UDP header computed over?",
        "explanation": "UDP checksum tính toán trên toàn bộ UDP segment (trừ trường checksum) cộng với một pseudo header chứa địa chỉ IP nguồn và đích.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "The entire UDP segment, except the checksum field itself."
          },
          {
            "key": "B",
            "text": "Just the UDP header but not the payload."
          },
          {
            "key": "C",
            "text": "The entire UDP segment, except the checksum field itself, and the IP sender and receive address fields"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 9,
        "week": 7,
        "question": "Phát biểu nào sau đây đúng về checksum?",
        "explanation": "Checksum dùng để phát hiện lỗi bit. Nếu checksum tính toán khác với giá trị nhận được thì có lỗi. Checksum được tính bằng cách cộng các byte (dưới dạng số 16-bit) và lấy bù 1.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "Người nhận gói có trường checksum sẽ cộng các byte nhận được, giống như người gửi đã làm và so sánh checksum được tính toán cục bộ này với giá trị checksum trong tiêu đề gói. Nếu hai giá trị này khác nhau thì người nhận biết rằng một trong các bit trong gói nhận được đã bị thay đổi trong quá trình truyền từ người gửi đến người nhận."
          },
          {
            "key": "B",
            "text": "Người nhận gói có checksum sẽ cộng các byte nhận được, giống như người gửi đã làm và so sánh checksum được tính toán cục bộ này với giá trị checksum trong tiêu đề gói. Nếu hai giá trị này giống nhau thì người nhận biết rằng tất cả các bit trong gói nhận được đều đúng, tức là không có bit nào bị thay đổi trong quá trình truyền từ người gửi đến người nhận."
          },
          {
            "key": "C",
            "text": "checksum được tính toán ở người gửi bằng cách coi mỗi byte trong gói là một số, sau đó cộng các số này (mỗi số đại diện cho một byte) lại với nhau để tính tổng (được gọi là checksum)."
          },
          {
            "key": "D",
            "text": "Giá trị checksum do người gửi tính toán thường được bao gồm trường checksum và tiêu đề gói."
          }
        ],
        "answer": [
          "A",
          "C",
          "D"
        ],
        "multi": true
      },
      {
        "id": 10,
        "week": 7,
        "question": "Tính giá trị Internet checksum cho hai từ 16 bit này: 11110101 11010011 và 10110011 01000100",
        "explanation": "Cộng hai số: 1111010111010011 + 1011001101000100 = 11010100100010111, bỏ qua bit carry (1) và cộng vào: 1010100100010111 + 1 = 1010100100011000, sau đó lấy bù 1: 0101011011100111.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "01010110 11101000"
          },
          {
            "key": "B",
            "text": "01010110 11100111"
          },
          {
            "key": "C",
            "text": "01101110 11010101"
          },
          {
            "key": "D",
            "text": "01011110 11000101"
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
        "question": "Tính giá trị Internet checksum cho hai từ 16 bit này: 01000001 11000100 và 00100000 00101011",
        "explanation": "Cộng hai số: 0100000111000100 + 0010000000101011 = 0110000011101111, lấy bù 1: 1001111000010000.",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "01101110 11010101"
          },
          {
            "key": "B",
            "text": "10011110 00010001"
          },
          {
            "key": "C",
            "text": "10011110 00010000"
          },
          {
            "key": "D",
            "text": "10011110 00001111"
          }
        ],
        "answer": [
          "C"
        ],
        "multi": false
      },
      {
        "id": 12,
        "week": 7,
        "question": "When computing the Internet checksum for two numbers, a single flipped bit (i.e., in just one of the two numbers) will always result in a changed checksum.",
        "explanation": "Một bit bị lật làm thay đổi tổng, do đó checksum thay đổi.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Đúng"
      },
      {
        "id": 13,
        "week": 7,
        "question": "When computing the Internet checksum for two numbers, a single flipped bit in each of the two numbers will always result in a changed checksum.",
        "explanation": "Nếu hai bit bị lật ở cùng vị trí trong hai số, tổng có thể không thay đổi.",
        "image_url": null,
        "type": "true_false",
        "subtype": "simple_tf",
        "answer": "Sai"
      },
      {
        "id": 14,
        "week": 7,
        "question": "Giả sử một phân đoạn UDP (A) đến máy chủ có địa chỉ IP là 128.119.40.186. Cổng nguồn trong phân đoạn UDP là 4829 và cổng đích là 3546. Địa chỉ IP của máy chủ gửi là 60.54.75.24. Gói trả lời (B) được gửi từ 128.119.40.186 về 60.54.75.24. Xác định: Địa chỉ IP đích, cổng đích, cổng nguồn, địa chỉ IP nguồn.",
        "explanation": "Trong gói trả lời, địa chỉ IP đích là của máy gửi ban đầu (60.54.75.24), cổng đích là cổng nguồn của gói đến (4829), cổng nguồn là cổng đích của gói đến (3546), địa chỉ IP nguồn là của máy chủ đang trả lời (128.119.40.186).",
        "image_url": null,
        "type": "matching",
        "subtype": "fill_blanks",
        "matches": [
          {
            "item": "Địa chỉ IP đích",
            "answer_key": "C",
            "answer_label": "60.54.75.24"
          },
          {
            "item": "Số cổng đích",
            "answer_key": "F",
            "answer_label": "4829"
          },
          {
            "item": "Số cổng nguồn",
            "answer_key": "B",
            "answer_label": "3546"
          },
          {
            "item": "Địa chỉ IP nguồn",
            "answer_key": "G",
            "answer_label": "128.119.40.186"
          }
        ],
        "choices": [
          {
            "key": "A",
            "label": "24"
          },
          {
            "key": "B",
            "label": "3546"
          },
          {
            "key": "C",
            "label": "60.54.75.24"
          },
          {
            "key": "D",
            "label": "80"
          },
          {
            "key": "E",
            "label": "10.0.0.1"
          },
          {
            "key": "F",
            "label": "4829"
          },
          {
            "key": "G",
            "label": "128.119.40.186"
          }
        ]
      },
      {
        "id": 15,
        "week": 7,
        "question": "Xem xét các mục đích/mục tiêu/việc sử dụng các cơ chế giao thức truyền dữ liệu đáng tin cậy khác nhau. Ghép với cơ chế RDT.",
        "explanation": "NAK báo lỗi, checksum phát hiện lỗi, sequence number phát hiện trùng lặp, retransmission khôi phục gói bị mất/hỏng, ACK xác nhận nhận đúng.",
        "image_url": null,
        "type": "matching",
        "subtype": "matching",
        "matches": [
          {
            "item": "Cho người gửi biết rằng gói KHÔNG được nhận chính xác ở người nhận",
            "answer_key": "E",
            "answer_label": "NAK"
          },
          {
            "item": "Được người gửi hoặc người nhận sử dụng để phát hiện các bit bị đảo lộn trong quá trình truyền gói tin",
            "answer_key": "C",
            "answer_label": "Checksum"
          },
          {
            "item": "Cho phép phát hiện sự trùng lặp ở người nhận",
            "answer_key": "D",
            "answer_label": "Sequence numbers"
          },
          {
            "item": "Cho phép người nhận cuối cùng nhận được gói tin bị hỏng hoặc bị mất trong lần truyền trước đó",
            "answer_key": "B",
            "answer_label": "Retransmission"
          },
          {
            "item": "Cho phép người gửi biết rằng gói tin đã được nhận chính xác tại người nhận",
            "answer_key": "A",
            "answer_label": "ACK"
          }
        ],
        "choices": [
          {
            "key": "A",
            "label": "ACK"
          },
          {
            "key": "B",
            "label": "Retransmission"
          },
          {
            "key": "C",
            "label": "Checksum"
          },
          {
            "key": "D",
            "label": "Sequence numbers"
          },
          {
            "key": "E",
            "label": "NAK"
          }
        ]
      },
      {
        "id": 16,
        "week": 7,
        "question": "Xem xét người gửi và người nhận rdt 2.0. Trình tự chuyển tiếp nào sau đây có thể xảy ra do lệnh gọi rdt_send() ban đầu và có thể xảy ra lỗi cũng như quá trình khôi phục lỗi sau đó.",
        "explanation": "Các trình tự hợp lệ trong rdt 2.0 bao gồm: gửi thành công (B), gửi có lỗi và NAK (D), gửi có lỗi và phục hồi (G).",
        "image_url": null,
        "type": "mcq",
        "subtype": "mcq",
        "options": [
          {
            "key": "A",
            "text": "S1, S2, S3"
          },
          {
            "key": "B",
            "text": "S1, R1, S2, R2, S3"
          },
          {
            "key": "C",
            "text": "S1, R2, S2"
          },
          {
            "key": "D",
            "text": "S1, R2, S3"
          },
          {
            "key": "E",
            "text": "S1, R1, S3"
          },
          {
            "key": "F",
            "text": "S1, R1, S2, R1, S3"
          },
          {
            "key": "G",
            "text": "S1, R1, S2"
          }
        ],
        "answer": [
          "B",
          "D",
          "G"
        ],
        "multi": true
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
