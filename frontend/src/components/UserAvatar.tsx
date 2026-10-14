import { useEffect, useState } from 'preact/hooks'
import * as TeamsGraph from '../teams-graph.ts'
import { initials } from '../utils/data.ts'
export function UserAvatar({ user, revision, large = false }) {
  const [failedSource, setFailedSource] = useState('')
  const [graphPhoto, setGraphPhoto] = useState('')
  useEffect(() => {
    if (!TeamsGraph.teamsGraphActive() || user.picture_url !== '/api/graph/picture/') return
    let active = true,
      objectUrl
    const controller = new AbortController()
    TeamsGraph.teamsGraphPhoto(controller.signal)
      .then((blob) => {
        if (active && blob) {
          objectUrl = URL.createObjectURL(blob)
          setGraphPhoto(objectUrl)
        }
      })
      .catch(() => {})
    return () => {
      active = false
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [user.id, user.picture_url, revision])
  const isLocalProfile = user.picture_url === '/api/profile/picture/'
  const isGraphProfile = user.picture_url === '/api/graph/picture/'
  const source = isLocalProfile
    ? `${user.picture_url}?v=${revision}`
    : isGraphProfile
      ? TeamsGraph.teamsGraphActive()
        ? graphPhoto
        : user.picture_url
      : ''
  return (
    <span class={`avatar user-avatar${large ? ' profile-avatar' : ''}`}>
      {source && source !== failedSource ? (
        <img
          src={source}
          alt={`${user.name || user.username}'s profile picture`}
          onError={() => setFailedSource(source)}
        />
      ) : (
        <span aria-label="Profile initials">{initials(user.name || user.username)}</span>
      )}
    </span>
  )
}
