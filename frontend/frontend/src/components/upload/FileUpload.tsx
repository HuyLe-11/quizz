import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { uploadPdf } from "../../services/api";

/**
 * PDF file upload component with drag & drop support.
 */
export function FileUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    setError(null);

    const droppedFile = e.dataTransfer.files[0];
    validateAndSetFile(droppedFile);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    if (e.target.files && e.target.files.length > 0) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (selectedFile: File) => {
    if (selectedFile.type !== "application/pdf") {
      setError("Please upload a PDF file.");
      return;
    }
    setFile(selectedFile);
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  const handleUpload = async () => {
    if (!file) return;

    setIsUploading(true);
    setError(null);

    try {
      const response = await uploadPdf(file);
      navigate(`/quiz/${response.exam_id}`);
    } catch (err: any) {
      setError(err.message || "Failed to upload file.");
      setIsUploading(false);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      {!file ? (
        <div
          className={`relative border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
            isDragging
              ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]"
              : "border-[var(--color-border-strong)] hover:border-[var(--color-primary)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            accept=".pdf"
            className="hidden"
          />
          <svg
            className="mx-auto h-12 w-12 text-[var(--color-text-muted)] mb-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <h3 className="mt-2 text-lg font-semibold text-[var(--color-text)]">
            Upload PDF
          </h3>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Drag and drop your file here, or click to browse
          </p>
        </div>
      ) : (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center space-x-3">
              <svg className="h-8 w-8 text-[var(--color-primary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <div>
                <p className="text-sm font-medium text-[var(--color-text)] truncate max-w-[200px] sm:max-w-xs">{file.name}</p>
                <p className="text-xs text-[var(--color-text-muted)]">{formatFileSize(file.size)}</p>
              </div>
            </div>
            <button
              onClick={() => setFile(null)}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-danger)] transition-colors p-1"
              disabled={isUploading}
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          
          <button
            onClick={handleUpload}
            disabled={isUploading}
            className="w-full py-2.5 px-4 bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)] text-white font-medium rounded-md transition-colors disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center"
          >
            {isUploading ? (
              <>
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Processing...
              </>
            ) : (
              "Generate Quiz"
            )}
          </button>
        </div>
      )}

      {error && (
        <div className="mt-4 p-3 bg-[var(--color-danger-light)] text-[var(--color-danger)] rounded-md text-sm text-center">
          {error}
        </div>
      )}
    </div>
  );
}
