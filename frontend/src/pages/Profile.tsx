import { useEffect, useRef, useState } from 'preact/hooks'
import { api, useResource } from '../utils/data.ts'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'
import { UserAvatar } from '../components/UserAvatar.tsx'

export function Profile({ user, revision, onUpdate }) {
  const resource = useResource('profile/')
  const isGraphProfile = !!(
    user.graph_enabled ||
    resource.data?.graph_enabled ||
    resource.data?.read_only
  )
  const [firstName, setFirstName] = useState(''),
    [lastName, setLastName] = useState('')
  const [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [success, setSuccess] = useState('')
  const [file, setFile] = useState<File | null>(null),
    [pictureUrl, setPictureUrl] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (resource.data) {
      setFirstName(resource.data.first_name)
      setLastName(resource.data.last_name)
      setPictureUrl(resource.data.picture_url)
    }
  }, [resource.data])
  async function update(action, path, options, message) {
    if (isGraphProfile) return
    setBusy(action)
    setError('')
    setSuccess('')
    try {
      const result = await api(path, options)
      if (action !== 'name') {
        setPictureUrl(action === 'remove' ? null : result.picture_url || '/api/profile/picture/')
        setFile(null)
        if (fileInput.current) fileInput.current.value = ''
      }
      await onUpdate()
      setSuccess(message)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }
  function chooseFile(e) {
    if (isGraphProfile) return
    const selected = e.currentTarget.files?.[0]
    setFile(null)
    setError('')
    setSuccess('')
    if (!selected) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type)) {
      setError('Choose a JPEG, PNG, or WEBP image.')
      e.currentTarget.value = ''
      return
    }
    if (selected.size > 5 * 1024 * 1024) {
      setError('Your picture must be no larger than 5 MiB.')
      e.currentTarget.value = ''
      return
    }
    setFile(selected)
  }
  function upload(e) {
    e.preventDefault()
    if (!file || busy || isGraphProfile) return
    const body = new FormData()
    body.append('picture', file)
    update('upload', 'profile/picture/', { method: 'POST', body }, 'Profile picture updated.')
  }
  if (resource.loading) return <Loading />
  if (resource.error) return <ErrorNotice error={resource.error} retry={resource.retry} />
  return (
    <>
      <PageHeading
        eyebrow="Your account"
        title="My profile"
        subtitle="Update your name and profile picture."
      />
      <ErrorNotice error={error} />
      {success && (
        <div class="success" role="status">
          {success}
        </div>
      )}
      <div class="detail-grid">
        <Panel title="Personal details">
          <div class="panel-body">
            <form
              class="form"
              aria-label="Personal details"
              aria-busy={!!busy}
              onSubmit={(e) => {
                e.preventDefault()
                if (!busy && !isGraphProfile)
                  update(
                    'name',
                    'profile/',
                    { method: 'PATCH', body: { first_name: firstName, last_name: lastName } },
                    'Profile details updated.',
                  )
              }}
            >
              <div class="form-grid">
                <label>
                  First name
                  <input
                    name="first_name"
                    autoComplete="given-name"
                    maxLength={150}
                    value={firstName}
                    disabled={!!busy || isGraphProfile}
                    onInput={(e) => setFirstName(e.currentTarget.value)}
                  />
                </label>
                <label>
                  Last name
                  <input
                    name="last_name"
                    autoComplete="family-name"
                    maxLength={150}
                    value={lastName}
                    disabled={!!busy || isGraphProfile}
                    onInput={(e) => setLastName(e.currentTarget.value)}
                  />
                </label>
              </div>
              <label>
                Username
                <input value={resource.data.username} readOnly autoComplete="username" />
              </label>
              <label>
                Email address
                <input value={resource.data.email || ''} readOnly autoComplete="email" />
              </label>
              <p class="form-help">
                Username and email are managed by your identity provider or administrator.
                {isGraphProfile &&
                  ' Your profile details are managed by Teams/Graph and cannot be edited here.'}
              </p>
              <p class="muted">Signed in with {user.auth_source || 'Local'}.</p>
              <div class="actions">
                <button class="button" disabled={!!busy || isGraphProfile}>
                  {busy === 'name' ? 'Saving…' : 'Save details'}
                </button>
              </div>
            </form>
          </div>
        </Panel>
        <Panel title="Profile picture">
          <div class="panel-body">
            <div class="profile-picture">
              <UserAvatar user={{ ...user, picture_url: pictureUrl }} revision={revision} large />
            </div>
            <form class="form" aria-label="Profile picture" aria-busy={!!busy} onSubmit={upload}>
              <label>
                Choose a picture
                <input
                  ref={fileInput}
                  name="picture"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  aria-describedby="picture-help"
                  disabled={!!busy || isGraphProfile}
                  onChange={chooseFile}
                />
              </label>
              <p class="form-help" id="picture-help">
                JPEG, PNG, or WEBP; up to 5 MiB and 4096 × 4096 pixels. Pictures are resized for
                your profile.
              </p>
              <div class="actions">
                <button class="button" disabled={!!busy || !file || isGraphProfile}>
                  {busy === 'upload' ? 'Uploading…' : 'Upload picture'}
                </button>
                <button
                  class="button secondary"
                  type="button"
                  disabled={!!busy || !pictureUrl || isGraphProfile}
                  onClick={() =>
                    update(
                      'remove',
                      'profile/picture/',
                      { method: 'DELETE' },
                      'Profile picture removed.',
                    )
                  }
                >
                  {busy === 'remove' ? 'Removing…' : 'Remove picture'}
                </button>
              </div>
            </form>
          </div>
        </Panel>
      </div>
    </>
  )
}
