export default function MessageBubble({ message }) {
  const classes = ['bubble', `bubble-${message.role}`]
  if (message.allowed === false) classes.push('bubble-denied')

  return (
    <div className={classes.join(' ')}>
      {message.pending ? (
        <span className="typing-dots">
          <span />
          <span />
          <span />
        </span>
      ) : (
        <pre className="bubble-text">{message.text}</pre>
      )}
    </div>
  )
}
