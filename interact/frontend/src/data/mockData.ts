/**
 * Mock data for development and testing.
 *
 * This file provides sample data matching the API response types
 * so the frontend can be developed independently of the backend.
 * Remove or disable this file once the backend is connected.
 */

import type { Subject, Exam, Question } from "../types/types";

// -- Mock Subjects --

export const MOCK_SUBJECTS: Subject[] = [
  {
    id: "net101",
    name: "Mạng máy tính",
    description: "Kiến thức mạng cơ bản, TCP/IP và giao thức HTTP",
    exam_count: 4,
    icon: "globe",
  },
  {
    id: "os201",
    name: "Hệ điều hành",
    description: "Quản lý tiến trình, bộ nhớ và hệ thống tệp",
    exam_count: 3,
    icon: "cpu",
  },
  {
    id: "db301",
    name: "Hệ quản trị cơ sở dữ liệu",
    description: "SQL, chuẩn hóa, giao dịch và lập chỉ mục",
    exam_count: 5,
    icon: "database",
  },
  {
    id: "algo401",
    name: "Cấu trúc dữ liệu và giải thuật",
    description: "Sắp xếp, tìm kiếm, cây, đồ thị và quy hoạch động",
    exam_count: 6,
    icon: "code",
  },
  {
    id: "se501",
    name: "Công nghệ phần mềm",
    description: "Vòng đời phát triển phần mềm, Agile, mẫu thiết kế và kiểm thử",
    exam_count: 2,
    icon: "layers",
  },
];

// -- Mock Questions --

const mockQuestions: Question[] = [
  // MCP - Single choice
  {
    question_id: 1,
    type: "mcp",
    multiple_choice: false,
    question:
      "Giao thức nào ở tầng giao vận đảm bảo dữ liệu được truyền tin cậy và đúng thứ tự?",
    options: [
      { label: "A", content: "UDP" },
      { label: "B", content: "TCP" },
      { label: "C", content: "ICMP" },
      { label: "D", content: "ARP" },
    ],
    option_answer: ["B"],
    explanation:
      "TCP (Giao thức điều khiển truyền vận) truyền luồng byte giữa các ứng dụng một cách tin cậy, đúng thứ tự và có kiểm tra lỗi. TCP sử dụng thông báo xác nhận, số thứ tự và cơ chế truyền lại để đảm bảo độ tin cậy.",
  },

  // MCP - Multiple choice
  {
    question_id: 2,
    type: "mcp",
    multiple_choice: true,
    question:
      "Đâu là đặc điểm của **chuyển mạch gói**? (Chọn tất cả đáp án đúng)",
    options: [
      { label: "A", content: "Tài nguyên được sử dụng khi cần, không được dành riêng trước" },
      { label: "B", content: "Được sử dụng trên Internet" },
      { label: "C", content: "FDM và TDM là hai phương thức triển khai" },
      {
        label: "D",
        content: "Có thể xảy ra mất gói do tắc nghẽn và độ trễ thay đổi",
      },
      { label: "E", content: "Dữ liệu có thể phải chờ trong hàng đợi trước khi truyền" },
    ],
    option_answer: ["A", "B", "D", "E"],
    explanation:
      "Chuyển mạch gói không dành riêng tài nguyên trước (khác với chuyển mạch kênh sử dụng FDM/TDM). Dữ liệu được chia thành các gói và có thể phải chờ tại bộ định tuyến, gây ra độ trễ thay đổi hoặc mất gói do tắc nghẽn.",
  },

  // True/False/Not Given
  {
    question_id: 3,
    type: "true_false_ng",
    multiple_choice: false,
    question:
      'Dịch vụ chuyển phát "nỗ lực tối đa" của tầng mạng có nghĩa là IP cố gắng chuyển các phân đoạn dữ liệu giữa các máy chủ giao tiếp nhưng không đảm bảo kết quả. Cụ thể, IP không đảm bảo phân đoạn sẽ được chuyển đến nơi, không đảm bảo thứ tự nhận và cũng không đảm bảo tính toàn vẹn của dữ liệu.',
    option_answer: ["true"],
    explanation:
      "Đây là mô tả chính xác về dịch vụ nỗ lực tối đa của IP. IP được thiết kế theo kiểu không kết nối và không đảm bảo độ tin cậy; các tầng cao hơn như TCP sẽ đảm nhiệm việc này.",
  },

  // Matching
  {
    question_id: 4,
    type: "matching",
    multiple_choice: false,
    question:
      "Ghép từng thành phần độ trễ gói tin với mô tả tương ứng.",
    matches: [
      {
        description:
          "Thời gian truyền các bit của gói tin lên đường truyền",
        answer: "Độ trễ truyền dẫn",
      },
      {
        description:
          "Thời gian để các bit truyền qua môi trường vật lý từ đầu này đến đầu kia",
        answer: "Độ trễ lan truyền",
      },
      {
        description:
          "Thời gian kiểm tra tính toàn vẹn, tra cứu bảng và chuyển gói tin từ cổng vào sang cổng ra",
        answer: "Độ trễ xử lý",
      },
      {
        description: "Thời gian gói tin chờ trong bộ đệm trước khi được truyền đi",
        answer: "Độ trễ hàng đợi",
      },
    ],
    explanation:
      "Bốn thành phần độ trễ gói tin gồm: xử lý (kiểm tra bit, xác định cổng ra), hàng đợi (chờ trong bộ đệm), truyền dẫn (đưa bit lên đường truyền, L/R) và lan truyền (thời gian truyền vật lý, d/s).",
  },

  // Fill-in
  {
    question_id: 5,
    type: "fill_in",
    multiple_choice: false,
    question:
      "Một gói tin có độ dài L = 1500 byte (1 byte = 8 bit) được truyền trên đường truyền có tốc độ R = 1 Gbps. Độ trễ truyền dẫn là bao nhiêu? Hãy trả lời bằng giây ở dạng ký hiệu khoa học (ví dụ: `1.2e-5`).",
    fill_in_answers: ["0.000012", "1.2e-5"],
    explanation:
      "Độ trễ truyền dẫn = L/R = (1500 * 8) bit / (10^9 bit/giây) = 12000 / 10^9 = 1.2 * 10^-5 giây = 0.000012 giây.",
  },

  // MCP with code in options
  {
    question_id: 6,
    type: "mcp",
    multiple_choice: false,
    question:
      "Đoạn mã Python sau in ra kết quả gì?\n\n```python\ndef foo(x, y=[]):\n    y.append(x)\n    return y\n\nprint(foo(1))\nprint(foo(2))\n```",
    options: [
      { label: "A", content: "`[1]` rồi đến `[2]`" },
      { label: "B", content: "`[1]` rồi đến `[1, 2]`" },
      { label: "C", content: "`[1]` rồi đến `[1]`" },
      { label: "D", content: "Lỗi" },
    ],
    option_answer: ["B"],
    explanation:
      "Trong Python, các đối số mặc định có thể thay đổi (như danh sách) được dùng chung giữa các lần gọi hàm. Danh sách `y` chỉ được tạo một lần khi định nghĩa hàm, nên những lần gọi sau sẽ nối thêm phần tử vào cùng danh sách đó. Đây là lỗi dễ gặp khi lập trình Python.",
  },

  // MCP with LaTeX
  {
    question_id: 7,
    type: "mcp",
    multiple_choice: false,
    question:
      "Giá trị của tích phân $\\int_0^1 x^2 \\, dx$ bằng bao nhiêu?",
    options: [
      { label: "A", content: "$\\frac{1}{2}$" },
      { label: "B", content: "$\\frac{1}{3}$" },
      { label: "C", content: "$\\frac{1}{4}$" },
      { label: "D", content: "$1$" },
    ],
    option_answer: ["B"],
    explanation:
      "Áp dụng quy tắc lũy thừa: $\\int_0^1 x^2 \\, dx = \\left[\\frac{x^3}{3}\\right]_0^1 = \\frac{1}{3} - 0 = \\frac{1}{3}$",
  },

  // True/False - false answer
  {
    question_id: 8,
    type: "true_false_ng",
    multiple_choice: false,
    question:
      "HTTP là giao thức có trạng thái và ghi nhớ các tương tác trước đó với máy khách qua nhiều yêu cầu.",
    option_answer: ["false"],
    explanation:
      "HTTP là giao thức phi trạng thái: máy chủ không lưu thông tin về các yêu cầu trước đó của máy khách. Trạng thái thường được duy trì bằng cookie hoặc mã phiên; chúng không thuộc phần cốt lõi của giao thức HTTP.",
  },
];

// -- Mock Exam --

export const MOCK_EXAM: Exam = {
  id: "exam-001",
  subject_id: "net101",
  title: "Mạng máy tính - Tuần 4",
  question_count: mockQuestions.length,
  questions: mockQuestions,
};

// -- Mock Exams List --

export const MOCK_EXAMS: Exam[] = [
  {
    id: "exam-001",
    subject_id: "net101",
    title: "Tuần 4 - Tầng mạng",
    question_count: 8,
    questions: mockQuestions,
  },
  {
    id: "exam-002",
    subject_id: "net101",
    title: "Tuần 5 - Tầng giao vận",
    question_count: 12,
    questions: [],
  },
  {
    id: "exam-003",
    subject_id: "net101",
    title: "Tuần 6 - Tầng ứng dụng",
    question_count: 15,
    questions: [],
  },
  {
    id: "exam-004",
    subject_id: "net101",
    title: "Tuần 7 - Bảo mật",
    question_count: 10,
    questions: [],
  },
];
