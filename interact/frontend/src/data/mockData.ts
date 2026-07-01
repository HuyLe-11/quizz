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
    name: "Computer Networks",
    description: "Networking fundamentals, TCP/IP, HTTP protocols",
    exam_count: 4,
    icon: "globe",
  },
  {
    id: "os201",
    name: "Operating Systems",
    description: "Process management, memory, file systems",
    exam_count: 3,
    icon: "cpu",
  },
  {
    id: "db301",
    name: "Database Systems",
    description: "SQL, normalization, transactions, indexing",
    exam_count: 5,
    icon: "database",
  },
  {
    id: "algo401",
    name: "Data Structures & Algorithms",
    description: "Sorting, searching, trees, graphs, dynamic programming",
    exam_count: 6,
    icon: "code",
  },
  {
    id: "se501",
    name: "Software Engineering",
    description: "SDLC, Agile, design patterns, testing",
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
      "Which transport layer protocol provides reliable, ordered delivery of data?",
    options: [
      { label: "A", content: "UDP" },
      { label: "B", content: "TCP" },
      { label: "C", content: "ICMP" },
      { label: "D", content: "ARP" },
    ],
    option_answer: ["B"],
    explanation:
      "TCP (Transmission Control Protocol) provides reliable, ordered, and error-checked delivery of a stream of bytes between applications. It uses acknowledgments, sequence numbers, and retransmission to ensure reliability.",
  },

  // MCP - Multiple choice
  {
    question_id: 2,
    type: "mcp",
    multiple_choice: true,
    question:
      "Which of the following are features of **packet switching**? (Select all that apply)",
    options: [
      { label: "A", content: "Resources are used on-demand, not reserved" },
      { label: "B", content: "Used on the Internet" },
      { label: "C", content: "FDM and TDM are two methods of implementation" },
      {
        label: "D",
        content: "Congestion loss and variable delay may occur",
      },
      { label: "E", content: "Data may queue before transmission" },
    ],
    option_answer: ["A", "B", "D", "E"],
    explanation:
      "Packet switching does not reserve resources (unlike circuit switching which uses FDM/TDM). Data is sent in packets that may queue at routers, causing variable delay and potential congestion loss.",
  },

  // True/False/Not Given
  {
    question_id: 3,
    type: "true_false_ng",
    multiple_choice: false,
    question:
      'The best-effort delivery service of the network layer means that IP makes its "best effort" to deliver segments between communicating hosts but makes no guarantees. In particular, it does not guarantee segment delivery, does not guarantee orderly delivery of segments, and does not guarantee the integrity of the data in the segments.',
    option_answer: ["true"],
    explanation:
      "This is a correct description of IP's best-effort service. IP is connectionless and unreliable by design - reliability is provided by upper layers like TCP.",
  },

  // Matching
  {
    question_id: 4,
    type: "matching",
    multiple_choice: false,
    question:
      "Match each component of packet delay with its correct description.",
    matches: [
      {
        description:
          "Time spent transmitting the bits of the packet onto the link",
        answer: "Transmission delay",
      },
      {
        description:
          "Time for bits to physically travel through the medium from one end to the other",
        answer: "Propagation delay",
      },
      {
        description:
          "Time needed to perform integrity checks, table lookups, and move packet from input to output link",
        answer: "Processing delay",
      },
      {
        description: "Time waiting in the packet buffer for link transmission",
        answer: "Queueing delay",
      },
    ],
    explanation:
      "The four components of packet delay are: processing (checking bits, determining output link), queueing (waiting in buffer), transmission (pushing bits onto link, L/R), and propagation (physical travel time, d/s).",
  },

  // Fill-in
  {
    question_id: 5,
    type: "fill_in",
    multiple_choice: false,
    question:
      "Given a packet of length L = 1500 bytes (1 byte = 8 bits) and a link transmission rate R = 1 Gbps, what is the transmission delay? Express your answer in seconds using scientific notation (e.g., `1.2e-5`).",
    fill_in_answers: ["0.000012", "1.2e-5"],
    explanation:
      "Transmission delay = L/R = (1500 * 8) bits / (10^9 bits/sec) = 12000 / 10^9 = 1.2 * 10^-5 seconds = 0.000012 seconds.",
  },

  // MCP with code in options
  {
    question_id: 6,
    type: "mcp",
    multiple_choice: false,
    question:
      "What is the output of the following Python code?\n\n```python\ndef foo(x, y=[]):\n    y.append(x)\n    return y\n\nprint(foo(1))\nprint(foo(2))\n```",
    options: [
      { label: "A", content: "`[1]` then `[2]`" },
      { label: "B", content: "`[1]` then `[1, 2]`" },
      { label: "C", content: "`[1]` then `[1]`" },
      { label: "D", content: "Error" },
    ],
    option_answer: ["B"],
    explanation:
      "In Python, default mutable arguments (like lists) are shared across function calls. The list `y` is created once when the function is defined, so subsequent calls append to the same list. This is a common Python gotcha.",
  },

  // MCP with LaTeX
  {
    question_id: 7,
    type: "mcp",
    multiple_choice: false,
    question:
      "What is the value of the integral $\\int_0^1 x^2 \\, dx$ ?",
    options: [
      { label: "A", content: "$\\frac{1}{2}$" },
      { label: "B", content: "$\\frac{1}{3}$" },
      { label: "C", content: "$\\frac{1}{4}$" },
      { label: "D", content: "$1$" },
    ],
    option_answer: ["B"],
    explanation:
      "Using the power rule: $\\int_0^1 x^2 \\, dx = \\left[\\frac{x^3}{3}\\right]_0^1 = \\frac{1}{3} - 0 = \\frac{1}{3}$",
  },

  // True/False - false answer
  {
    question_id: 8,
    type: "true_false_ng",
    multiple_choice: false,
    question:
      "HTTP is a stateful protocol that remembers previous interactions with clients across multiple requests.",
    option_answer: ["false"],
    explanation:
      "HTTP is stateless - the server does not retain any information about past client requests. State is typically maintained using cookies or session tokens, which are not part of the core HTTP protocol.",
  },
];

// -- Mock Exam --

export const MOCK_EXAM: Exam = {
  id: "exam-001",
  subject_id: "net101",
  title: "Computer Networks - Week 4",
  question_count: mockQuestions.length,
  questions: mockQuestions,
};

// -- Mock Exams List --

export const MOCK_EXAMS: Exam[] = [
  {
    id: "exam-001",
    subject_id: "net101",
    title: "Week 4 - Network Layer",
    question_count: 8,
    questions: mockQuestions,
  },
  {
    id: "exam-002",
    subject_id: "net101",
    title: "Week 5 - Transport Layer",
    question_count: 12,
    questions: [],
  },
  {
    id: "exam-003",
    subject_id: "net101",
    title: "Week 6 - Application Layer",
    question_count: 15,
    questions: [],
  },
  {
    id: "exam-004",
    subject_id: "net101",
    title: "Week 7 - Security",
    question_count: 10,
    questions: [],
  },
];
