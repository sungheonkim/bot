def calculate_average(numbers):
    # 빈 리스트가 들어왔을 때 ZeroDivisionError 예외처리가 안 되어있습니다.
    total = sum(numbers)
    count = len(numbers)
    return total / count

def risky_loop():
    items = [1, 2, 3]
    # IndexError를 유발하는 잘못된 인덱스 접근
    for i in range(10):
        print(items[i])

if __name__ == "__main__":
    calculate_average([]) 
    risky_loop()
