FROM python:3.12-slim
WORKDIR /workspace
COPY services/order/requirements.txt /tmp/order-requirements.txt
COPY services/payment/requirements.txt /tmp/payment-requirements.txt
RUN pip install --no-cache-dir -r /tmp/order-requirements.txt -r /tmp/payment-requirements.txt pytest==8.3.5
COPY services/order ./services/order
COPY services/payment ./services/payment
COPY tests/member_b ./tests/member_b
CMD ["python", "-m", "pytest", "-q", "tests/member_b"]
