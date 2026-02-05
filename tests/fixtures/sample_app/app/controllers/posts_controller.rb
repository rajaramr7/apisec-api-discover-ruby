class PostsController < ApplicationController
  before_action :set_post, only: [:show, :edit, :update, :destroy]
  skip_before_action :authenticate_user!, only: [:index, :show]

  def index; end
  def show; end
  def new; end
  def create; end
  def edit; end
  def update; end
  def destroy; end
  def publish; end
  def drafts; end

  private

  def post_params
    params.require(:post).permit(:title, :body, :published)
  end

  def set_post
    @post = Post.find(params[:id])
  end
end
