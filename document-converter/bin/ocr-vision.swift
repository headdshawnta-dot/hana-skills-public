import Foundation
import Vision
import PDFKit
import AppKit

let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let pdf = PDFDocument(url: url) else {
    FileHandle.standardError.write("FAILED to load PDF".data(using: .utf8)!)
    exit(1)
}

var allText = "<!-- auto-OCR via macOS Vision @ \(Date()) -->\n\n"
allText += "<!-- pages: \(pdf.pageCount) | source: \(CommandLine.arguments[1]) -->\n\n"

for i in 0..<pdf.pageCount {
    guard let page = pdf.page(at: i) else { continue }
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.0
    let pixW = Int(bounds.width * scale)
    let pixH = Int(bounds.height * scale)
    let cs = CGColorSpaceCreateDeviceRGB()
    let info = CGImageAlphaInfo.premultipliedLast.rawValue
    guard let ctxRef = CGContext(data: nil, width: pixW, height: pixH, bitsPerComponent: 8, bytesPerRow: 0, space: cs, bitmapInfo: info) else {
        continue
    }
    ctxRef.setFillColor(NSColor.white.cgColor)
    ctxRef.fill(CGRect(x: 0, y: 0, width: pixW, height: pixH))
    ctxRef.scaleBy(x: scale, y: scale)
    let nsCtx = NSGraphicsContext(cgContext: ctxRef, flipped: false)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = nsCtx
    page.draw(with: .mediaBox, to: ctxRef)
    NSGraphicsContext.restoreGraphicsState()
    guard let cgImage = ctxRef.makeImage() else { continue }
    
    let request = VNRecognizeTextRequest { req, _ in
        // callback (not awaited here)
    }
    request.recognitionLevel = .accurate
    if #available(macOS 13.0, *) {
        request.recognitionLanguages = ["zh-Hans", "en-US"]
    }
    
    let handler = VNImageRequestHandler(cgImage: cgImage)
    do {
        try handler.perform([request])
        if let results = request.results as? [VNRecognizedTextObservation] {
            allText += "<!-- page \(i+1) -->\n\n"
            for obs in results {
                if let top = obs.topCandidates(1).first {
                    allText += top.string + "\n"
                }
            }
            allText += "\n"
        }
    } catch {
        FileHandle.standardError.write("ocr fail p\(i): \(error)".data(using: .utf8)!)
    }
}

print(allText)
