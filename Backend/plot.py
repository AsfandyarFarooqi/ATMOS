
import requests
import matplotlib.pyplot as plt
from PIL import Image
from io import BytesIO


def display_agb_image(url):
    # Fetch image from URL
    response = requests.get(url)
    img = Image.open(BytesIO(response.content))

    # Display with matplotlib
    plt.figure(figsize=(8, 8))
    plt.imshow(img)
    plt.title("Above Ground Biomass (AGB)")
    plt.axis('off')
    plt.show()

