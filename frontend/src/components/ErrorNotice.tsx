// Displays an error message with an optional retry action.
interface ErrorNoticeProps {
  error?: string | null
  retry?: () => void
}

export function ErrorNotice({ error, retry }: ErrorNoticeProps) {
  return (
    error && (
      <div class="error" role="alert">
        <span>{error}</span>
        {retry && (
          <button class="button secondary" onClick={retry}>
            Try again
          </button>
        )}
      </div>
    )
  )
}
