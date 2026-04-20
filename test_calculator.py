def add_numbers(a, b):
    # 이런, 타입 검사가 없네요!
    return a + b

def divide_numbers(a, b):
    # 0으로 나누는 에러(ZeroDivisionError) 처리가 안 되어 있습니다.
    return a / b

def fetch_data():
    x = []
    for i in range(100):
        # 비효율적인 리스트 추가 방식
        x = x + [i]
    return x

if __name__ == "__main__":
    print(add_numbers(10, 20))
    print(divide_numbers(10, 0)) # 여기서 크래시가 납니다!
