import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from common.error_code import ToolErrorCode


async def send_gmail(
    to: str,
    subject: str,
    body: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    sender_email: str = None,
    sender_password: str = None,
) -> str:
    """
    Gmail SMTP를 통해 이메일을 발송합니다.

    Args:
        to: 수신자 이메일 주소
        subject: 이메일 제목
        body: 이메일 본문
        smtp_host: SMTP 서버 호스트 (기본값: smtp.gmail.com)
        smtp_port: SMTP 서버 포트 (기본값: 587)
        sender_email: 발신자 이메일 주소
        sender_password: 발신자 앱 비밀번호

    Returns:
        발송 성공 여부 메시지
    """
    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to, msg.as_string())

        return json.dumps({"success": True, "message": f"{to}로 이메일 발송 성공"}, ensure_ascii=False)
    except smtplib.SMTPAuthenticationError:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Gmail: 인증 오류)"}, ensure_ascii=False)
    except smtplib.SMTPException as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Gmail: {str(e)})"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Gmail: {str(e)})"}, ensure_ascii=False)
