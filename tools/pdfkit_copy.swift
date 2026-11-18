// PDFKit（macOS プレビューと同じ文字選択エンジン）で、矩形選択した文字列を返す。
// 入力（標準入力 JSON）: {"pdf": "path", "queries": [{"id": "...", "page": 0, "rect": [x0, y0, x1, y1]}], "page_text": [0, 1]}
//   rect は PDF ユーザー空間（左下原点、回転前）。
// 出力（標準出力 JSON）: {"selections": {"id": "text" | null}, "page_text": {"0": "..."}}
// GUI 操作ではないため、プレビューでの手動確認の代わりにはならない。
import Foundation
import PDFKit

struct Query: Decodable { let id: String; let page: Int; let rect: [Double] }
struct Input: Decodable { let pdf: String; let queries: [Query]; let page_text: [Int]? }

let data = FileHandle.standardInput.readDataToEndOfFile()
guard let input = try? JSONDecoder().decode(Input.self, from: data) else {
    FileHandle.standardError.write("invalid input json\n".data(using: .utf8)!); exit(2)
}
guard let doc = PDFDocument(url: URL(fileURLWithPath: input.pdf)) else {
    FileHandle.standardError.write("cannot open pdf\n".data(using: .utf8)!); exit(3)
}
var selections: [String: Any] = [:]
for q in input.queries {
    guard q.page >= 0, q.page < doc.pageCount, let page = doc.page(at: q.page), q.rect.count == 4 else {
        selections[q.id] = NSNull(); continue
    }
    let r = CGRect(x: q.rect[0], y: q.rect[1], width: q.rect[2] - q.rect[0], height: q.rect[3] - q.rect[1])
    if let s = page.selection(for: r)?.string { selections[q.id] = s } else { selections[q.id] = NSNull() }
}
var pageText: [String: Any] = [:]
for p in input.page_text ?? [] {
    if p >= 0, p < doc.pageCount, let page = doc.page(at: p) { pageText[String(p)] = page.string ?? NSNull() }
}
let out: [String: Any] = ["selections": selections, "page_text": pageText,
                          "os": ProcessInfo.processInfo.operatingSystemVersionString]
let json = try JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
FileHandle.standardOutput.write(json)
